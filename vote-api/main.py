from fastapi import FastAPI, Query, HTTPException, status, Depends
import redis
import os
from pymongo import MongoClient
from pymongo.results import UpdateResult, DeleteResult
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import datetime
from bson import ObjectId
from pydantic_core import core_schema
from pydantic.json_schema import JsonSchemaValue
from pydantic import GetJsonSchemaHandler
from jose import JWTError, jwt

app = FastAPI()

# --- JWT Configuration (should match auth-api) ---
JWT_SECRET_KEY = os.environ.get("JWT_SECRET", "yoursupersecretkey") # Ensure this matches auth-api
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # The URL doesn't matter much here as we're not using FastAPI's built-in form login

class TokenData(BaseModel):
    sub: Optional[str] = None # 'sub' usually holds the username or user ID
    role: Optional[str] = None
    id: Optional[int] = None # or str, depending on what auth-api puts in 'id'

class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ) -> core_schema.CoreSchema:
        def validate_from_any(value: Any) -> ObjectId:
            if isinstance(value, ObjectId):
                return value
            if isinstance(value, str) and ObjectId.is_valid(value):
                return ObjectId(value)
            raise ValueError(f"Invalid ObjectId: {value}")

        return core_schema.union_schema(
            [
                core_schema.is_instance_schema(ObjectId),
                core_schema.no_info_plain_validator_function(validate_from_any),
            ],
            serialization=core_schema.plain_serializer_function_ser_schema(lambda x: str(x))
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema_obj: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema = handler(core_schema.str_schema())
        json_schema.update(
            pattern=r"^[0-9a-fA-F]{24}$",
        )
        return json_schema

class PollBase(BaseModel):
    question: str
    options: List[str] = Field(..., min_items=2)

class PollCreate(PollBase):
    pass

r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

client = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = client.voting
poll_collection = db.polls
user_votes_collection = db.user_votes # New collection to track votes per user
results_collection = db.results

class PollInDB(PollBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id") # type: ignore
    is_active: bool = False
    created_at: datetime

    class Config:
        allow_population_by_field_name = True

# --- JWT Dependency ---
async def get_current_user_token_data(token: str = Depends(oauth2_scheme)) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        user_id: int = payload.get("id") # or str
        if username is None or role is None:
            raise credentials_exception
        token_data = TokenData(sub=username, role=role, id=user_id)
    except JWTError:
        raise credentials_exception
    return token_data

async def require_admin_user(current_user: TokenData = Depends(get_current_user_token_data)):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted. Admin access required.",
        )
    return current_user

# --- Poll Management Endpoints (Admin Only) ---
@app.post("/polls", response_model=PollInDB, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin_user)])
async def create_poll(poll_data: PollCreate, current_user: TokenData = Depends(require_admin_user)):
    # current_user is available if needed, e.g., for logging who created the poll
    new_poll_doc = {
        "question": poll_data.question,
        "options": poll_data.options,
        "is_active": False,
        "created_at": datetime.utcnow()
    }
    inserted_result = poll_collection.insert_one(new_poll_doc)
    created_poll = poll_collection.find_one({"_id": inserted_result.inserted_id})
    return created_poll

@app.get("/polls", response_model=List[PollInDB], dependencies=[Depends(require_admin_user)])
async def list_polls(current_user: TokenData = Depends(require_admin_user)):
    polls = list(poll_collection.find({}).sort("created_at", -1))
    return polls

@app.put("/polls/{poll_id}/activate", status_code=status.HTTP_200_OK, dependencies=[Depends(require_admin_user)])
async def activate_poll(poll_id: str, current_user: TokenData = Depends(require_admin_user)):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")
    
    object_id_to_activate = ObjectId(poll_id)    

    # No longer deactivating all other polls.
    # poll_collection.update_many({}, {"$set": {"is_active": False}})
    
    update_result: UpdateResult = poll_collection.update_one(
        {"_id": object_id_to_activate},
        {"$set": {"is_active": True}}
    )

    if update_result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    newly_active_poll = poll_collection.find_one({"_id": object_id_to_activate})
    if newly_active_poll:
        for option in newly_active_poll.get("options", []):
            # Use poll-specific Redis keys
            r.set(f"vote:{str(object_id_to_activate)}:{option}", 0)
        
        initial_snapshot = {opt: 0 for opt in newly_active_poll.get("options", [])}
        initial_snapshot["_poll_question"] = newly_active_poll.get("question")
        initial_snapshot["_poll_id"] = str(object_id_to_activate) # Add poll_id to results
        initial_snapshot["_timestamp"] = datetime.utcnow()
        results_collection.insert_one(initial_snapshot)
        return {"message": f"Poll '{newly_active_poll.get('question')}' activated successfully."}
    
    return {"message": "Poll activated, but could not fetch details to initialize votes."}

@app.put("/polls/{poll_id}/deactivate", status_code=status.HTTP_200_OK, dependencies=[Depends(require_admin_user)])
async def deactivate_poll(poll_id: str, current_user: TokenData = Depends(require_admin_user)):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")
    
    object_id_to_deactivate = ObjectId(poll_id)

    update_result: UpdateResult = poll_collection.update_one(
        {"_id": object_id_to_deactivate},
        {"$set": {"is_active": False}}
    )

    if update_result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    # Optionally, clear Redis votes for this poll upon deactivation
    # poll_to_clear = poll_collection.find_one({"_id": object_id_to_deactivate})
    # if poll_to_clear:
    #     for option in poll_to_clear.get("options", []):
    #         r.delete(f"vote:{str(object_id_to_deactivate)}:{option}")
    return {"message": f"Poll with ID {poll_id} deactivated successfully."}

@app.delete("/polls/{poll_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(require_admin_user)])
async def delete_poll_by_id(poll_id: str, current_user: TokenData = Depends(require_admin_user)):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")

    object_id_to_delete = ObjectId(poll_id)
    poll_to_delete = poll_collection.find_one({"_id": object_id_to_delete})

    if not poll_to_delete:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    if poll_to_delete.get("is_active"):
        for option in poll_to_delete.get("options", []):
            # Use poll-specific Redis keys
            r.delete(f"vote:{str(object_id_to_delete)}:{option}")

    delete_result: DeleteResult = poll_collection.delete_one({"_id": object_id_to_delete})
    
    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found during delete operation")

    return {"message": f"Poll '{poll_to_delete.get('question')}' deleted successfully"}
    # Also clear user_votes for the deleted poll
    user_votes_collection.delete_many({"poll_id": object_id_to_delete})

# --- Public/User Endpoints ---
@app.get("/polls/active", response_model=List[PollInDB]) # Returns a list of active polls
async def get_active_polls():
    active_polls = list(poll_collection.find({"is_active": True}).sort("created_at", -1))
    if not active_polls:
        # Return empty list if no active polls, or raise 404 if that's preferred
        return [] 
    return active_polls

@app.post("/vote", dependencies=[Depends(get_current_user_token_data)])
def cast_vote(
    poll_id: str = Query(..., description="The ID of the poll to vote on"),
    option_voted: str = Query(..., alias="option", description="The option being voted for"),
    current_user: TokenData = Depends(get_current_user_token_data)
):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")
    
    target_poll_id = ObjectId(poll_id)
    poll_to_vote_on = poll_collection.find_one({"_id": target_poll_id, "is_active": True})

    if not poll_to_vote_on:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active poll with the given ID not found or is not active.")
    
    user_id = current_user.id 
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User identifier not found in token.")

    # Check if the user has already voted for this poll
    existing_vote = user_votes_collection.find_one({"user_id": user_id, "poll_id": target_poll_id})
    if existing_vote:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have already voted in this poll.")

    poll_options = poll_to_vote_on.get("options", [])
    if option_voted not in poll_options:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid option. Valid options are: {', '.join(poll_options)}")

    r.incr(f"vote:{str(target_poll_id)}:{option_voted}") # Poll-specific Redis key

    user_votes_collection.insert_one({
        "user_id": user_id,
        "poll_id": target_poll_id,
        "option_voted": option_voted,
        "timestamp": datetime.utcnow()
    })
    return {"message": f"Vote for {option_voted} recorded."}

@app.get("/health") # Health check endpoint
async def health_check():
    return {"status": "healthy"}

origins = [
    "http://10.25.156.34:31340", # The origin of your frontend
    "http://10.25.156.39:31340", # Add other node IPs if you access frontend via them
    "http://10.25.156.40:31340", 
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
