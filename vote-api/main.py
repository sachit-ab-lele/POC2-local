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
import firebase_admin
from firebase_admin import credentials, auth

app = FastAPI()

# --- Firebase Initialization for Authentication ---
try:
    cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH", "firebase-service-account.json")
    if not os.path.exists(cred_path):
        print(f"Warning: Firebase service account key not found at {cred_path}. Login will not work.")
        # Allow app to run for other functionalities, but Firebase auth will fail.
    else:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
# --- End Firebase Initialization ---

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
results_collection = db.results

class PollInDB(PollBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id") # type: ignore
    is_active: bool = False
    created_at: datetime

    class Config:
        allow_population_by_field_name = True

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    # For Firebase Auth, 'username' is typically an email.
    # Firebase Admin SDK doesn't have a direct "sign in with email and password" method
    # for server-side validation without client-side token generation.
    # A common server-side pattern is to verify an ID token sent from the client
    # after the client signs in using Firebase client SDKs.

    # However, if you MUST validate email/password directly on the server without a client token,
    # it's more complex and less secure as you'd be handling passwords directly.
    # The recommended Firebase flow is client-side sign-in, then send ID token to backend.

    # For this example, let's assume you want to check if a user exists
    # and assign a role based on a custom claim or a predefined admin email.
    # THIS IS NOT A SECURE LOGIN IF YOU DON'T VERIFY THE PASSWORD.
    # Firebase's primary server-side auth is ID token verification.

    # To truly use Firebase for login, the frontend would use Firebase JS SDK to sign in,
    # get an ID token, and send that token to this backend endpoint for verification.
    # For now, let's simulate a basic check and role assignment.
    # Replace this with proper ID token verification in a real scenario.

    try:
        user = auth.get_user_by_email(username) # Assuming username is an email
        # In a real app, you'd verify the password here if not using ID tokens,
        # but Firebase Admin SDK doesn't provide a direct password check.
        # This is a placeholder for demonstration.
        # For a simple role system, you could check custom claims or a hardcoded admin email.
        role = "admin" if user.email == os.getenv("ADMIN_EMAIL", "admin@example.com") else "user"
        return {"message": "Login successful (simulated)", "role": role, "uid": user.uid}
    except auth.UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        # Log the exception e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login error: {str(e)}",
        )

@app.post("/polls", response_model=PollInDB, status_code=status.HTTP_201_CREATED)
async def create_poll(poll_data: PollCreate):
    new_poll_doc = {
        "question": poll_data.question,
        "options": poll_data.options,
        "is_active": False,
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

    currently_active_poll = poll_collection.find_one({"is_active": True})
    if currently_active_poll:
        for option in currently_active_poll.get("options", []):
            r.delete(f"vote:{option}")

    poll_collection.update_many({}, {"$set": {"is_active": False}})
    
    update_result: UpdateResult = poll_collection.update_one(
        {"_id": object_id_to_activate},
        {"$set": {"is_active": True}}
    )

    if update_result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    newly_active_poll = poll_collection.find_one({"_id": object_id_to_activate})
    if newly_active_poll:
        for option in newly_active_poll.get("options", []):
            r.set(f"vote:{option}", 0)
        
        initial_snapshot = {opt: 0 for opt in newly_active_poll.get("options", [])}
        initial_snapshot["_poll_question"] = newly_active_poll.get("question")
        initial_snapshot["_timestamp"] = datetime.utcnow()
        results_collection.insert_one(initial_snapshot)
        return {"message": f"Poll '{newly_active_poll.get('question')}' activated successfully."}
    
    return {"message": "Poll activated, but could not fetch details to initialize votes."}

@app.delete("/polls/{poll_id}", status_code=status.HTTP_200_OK)
async def delete_poll_by_id(poll_id: str):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")

    object_id_to_delete = ObjectId(poll_id)
    poll_to_delete = poll_collection.find_one({"_id": object_id_to_delete})

    if not poll_to_delete:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found")

    if poll_to_delete.get("is_active"):
        for option in poll_to_delete.get("options", []):
            r.delete(f"vote:{option}")

    delete_result: DeleteResult = poll_collection.delete_one({"_id": object_id_to_delete})
    
    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found during delete operation")

    return {"message": f"Poll '{poll_to_delete.get('question')}' deleted successfully"}

@app.get("/polls/active")
async def get_active_poll_details():
    active_poll = poll_collection.find_one({"is_active": True}, {"_id": 0, "is_active": 0, "created_at": 0})
    if not active_poll:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active poll found.")
    return active_poll

@app.post("/vote")
def cast_vote(option_voted: str = Query(..., alias="option")):
    active_poll = poll_collection.find_one({"is_active": True})
    if not active_poll:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active poll to vote on.")
    
    poll_options = active_poll.get("options", [])
    if option_voted not in poll_options:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid option. Valid options are: {', '.join(poll_options)}")

    r.incr(f"vote:{option_voted}")

    current_votes_snapshot = {opt: int(r.get(f"vote:{opt}") or 0) for opt in poll_options}
    current_votes_snapshot["_poll_question"] = active_poll.get("question")
    current_votes_snapshot["_timestamp"] = datetime.utcnow()
    results_collection.insert_one(current_votes_snapshot)
    return {"message": f"Vote for {option_voted} recorded."}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
