from fastapi import FastAPI, Query, HTTPException, status
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware
from bson import ObjectId 
import redis
import os

app = FastAPI()


client = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = client.voting
poll_collection = db.polls


r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

@app.get("/results")
def get_results(poll_id: str = Query(..., description="The ID of the poll to fetch results for")):
    if not ObjectId.is_valid(poll_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Poll ID format")

    target_poll_object_id = ObjectId(poll_id)
    poll = poll_collection.find_one({"_id": target_poll_object_id})

    if not poll:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Poll with ID {poll_id} not found.")

    options = poll.get("options", [])
    if not options:
        return {} 

    vote_counts = {}
    for option in options:
        count = r.get(f"vote:{poll_id}:{option}") 
        vote_counts[option] = int(count) if count is not None else 0
    return vote_counts

origins = [
    "http://10.25.156.34:31340", 
    "http://10.25.156.39:31340", 
    "http://10.25.156.40:31340", 
    "http://localhost:8080",
    "http://10.25.156.163", 
    "http://10.25.156.89"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
