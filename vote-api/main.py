from fastapi import FastAPI, Query, Form, HTTPException, status
import redis
import os
from pymongo import MongoClient
from pymongo.results import UpdateResult, DeleteResult
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import datetime
from bson import ObjectId
from pydantic_core import core_schema
from pydantic.json_schema import JsonSchemaValue
from pydantic import GetJsonSchemaHandler

app = FastAPI()

# Helper for Pydantic to work with MongoDB ObjectId
class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ) -> core_schema.CoreSchema:
        """
        Defines how Pydantic should validate and serialize instances of PyObjectId.
        It handles:
        1. Validation of incoming Python ObjectId instances.
        2. Validation of incoming strings (from JSON) and conversion to ObjectId.
        3. Serialization of ObjectId instances to strings for JSON output.
        """
        def validate_from_any(value: Any) -> ObjectId:
            """Attempts to validate/convert the value to an ObjectId."""
            if isinstance(value, ObjectId): # If it's already an ObjectId (or PyObjectId instance)
                return value
            if isinstance(value, str) and ObjectId.is_valid(value):
                return ObjectId(value)
            raise ValueError(f"Invalid ObjectId: {value}")

        # Schema for validating Python input and JSON input (string that is a valid ObjectId)
        # For serialization, it will convert ObjectId to string.
        return core_schema.union_schema(
            [
                core_schema.is_instance_schema(ObjectId), # Accepts ObjectId instances directly
                core_schema.no_info_plain_validator_function(validate_from_any), # Validates other types
            ],
            serialization=core_schema.plain_serializer_function_ser_schema(lambda x: str(x))
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema_obj: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema = handler(core_schema.str_schema())
        json_schema.update(
            pattern=r"^[0-9a-fA-F]{24}$", # Regex for ObjectId string
        )
        return json_schema

class PollBase(BaseModel):
    question: str
    options: List[str] = Field(..., min_items=2)

class PollCreate(PollBase):
    pass

# Redis client
# Initialize Redis client globally, connecting to the 'redis' service on its internal port 6379
r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

# MongoDB client
# Initialize MongoDB client globally, connecting to the 'mongo' service on its internal port 27017
client = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = client.voting
poll_collection = db.polls # Collection for poll definitions
results_collection = db.results # Collection for vote snapshots

class PollInDB(PollBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    is_active: bool = False
    created_at: datetime

    class Config:
        allow_population_by_field_name = True

# Dummy user store for demonstration
DUMMY_USERS_DB = {
    "admin": "admin",
    "user": "password" # Changed from "user":"user" to "user1":"password" for clarity
}

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user_password = DUMMY_USERS_DB.get(username)
    if not user_password or user_password != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = "admin" if username == "admin" else "user"
    return {"message": "Login successful", "role": role}

@app.post("/polls", response_model=PollInDB, status_code=status.HTTP_201_CREATED)
async def create_poll(poll_data: PollCreate):
    new_poll_doc = {
        "question": poll_data.question,
        "options": poll_data.options,
        "is_active": False, # New polls are inactive by default
        "created_at": datetime.utcnow()
    }
    inserted_result = poll_collection.insert_one(new_poll_doc)
    created_poll = poll_collection.find_one({"_id": inserted_result.inserted_id})
    return created_poll

@app.get("/polls", response_model=List[PollInDB])
async def list_polls():
    polls = list(poll_collection.find({}).sort("created_at", -1))
    return polls

@app.put("/polls/{poll_id}/activate", status_code=status.HTTP_200_OK)
async def activate_poll(poll_id: str):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")
    
    object_id_to_activate = ObjectId(poll_id)

    # Clear Redis votes for any currently active poll
    currently_active_poll = poll_collection.find_one({"is_active": True})
    if currently_active_poll:
        for option in currently_active_poll.get("options", []):
            r.delete(f"vote:{option}")

    # Deactivate all polls
    poll_collection.update_many({}, {"$set": {"is_active": False}})
    
    # Activate the target poll
    update_result: UpdateResult = poll_collection.update_one(
        {"_id": object_id_to_activate},
        {"$set": {"is_active": True}}
    )

    if update_result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    # Initialize Redis counters and results snapshot for the newly activated poll
    newly_active_poll = poll_collection.find_one({"_id": object_id_to_activate})
    if newly_active_poll:
        for option in newly_active_poll.get("options", []):
            r.set(f"vote:{option}", 0)
        
        initial_snapshot = {opt: 0 for opt in newly_active_poll.get("options", [])}
        initial_snapshot["_poll_question"] = newly_active_poll.get("question")
        initial_snapshot["_timestamp"] = datetime.utcnow()
        results_collection.insert_one(initial_snapshot)
        return {"message": f"Poll '{newly_active_poll.get('question')}' activated successfully."}
    
    return {"message": "Poll activated, but could not fetch details to initialize votes."} # Should not happen if update was successful

@app.delete("/polls/{poll_id}", status_code=status.HTTP_200_OK)
async def delete_poll_by_id(poll_id: str):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")

    object_id_to_delete = ObjectId(poll_id)
    poll_to_delete = poll_collection.find_one({"_id": object_id_to_delete})

    if not poll_to_delete:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    # If the poll being deleted was active, clear its Redis votes
    if poll_to_delete.get("is_active"):
        for option in poll_to_delete.get("options", []):
            r.delete(f"vote:{option}")

    delete_result: DeleteResult = poll_collection.delete_one({"_id": object_id_to_delete})
    
    if delete_result.deleted_count == 0: # Should not happen if find_one found it
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found during delete operation")

    return {"message": f"Poll '{poll_to_delete.get('question')}' deleted successfully"}

@app.get("/polls/active") # Renamed from /poll/active for consistency
async def get_active_poll_details():
    active_poll = poll_collection.find_one({"is_active": True}, {"_id": 0, "is_active": 0, "created_at": 0}) # Exclude _id for this specific response
    if not active_poll:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active poll found.")
    return active_poll

@app.post("/vote")
def cast_vote(option_voted: str = Query(..., alias="option")): # 'option' is the query param name expected by frontend
    active_poll = poll_collection.find_one({"is_active": True})
    if not active_poll:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active poll to vote on.")
    
    poll_options = active_poll.get("options", [])
    if option_voted not in poll_options:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid option. Valid options are: {', '.join(poll_options)}")

    # Increment Redis counter
    r.incr(f"vote:{option_voted}")

    # Store snapshot in MongoDB
    current_votes_snapshot = {opt: int(r.get(f"vote:{opt}") or 0) for opt in poll_options}
    current_votes_snapshot["_poll_question"] = active_poll.get("question")
    current_votes_snapshot["_timestamp"] = datetime.utcnow()
    results_collection.insert_one(current_votes_snapshot)
    return {"message": f"Vote for {option_voted} recorded."}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"], # Frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
