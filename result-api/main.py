from fastapi import FastAPI
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware
import redis
import os

app = FastAPI()

# MongoDB Connection
client = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = client.voting
poll_collection = db.polls

# Redis Connection
r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

@app.get("/results")
def get_results():
    active_poll = poll_collection.find_one({"is_active": True})
    if not active_poll:
        return {} # Or {"error": "No active poll"}

    options = active_poll.get("options", [])
    if not options:
        return {} # Or {"error": "Active poll has no options"}

    vote_counts = {}
    for option in options:
        count = r.get(f"vote:{option}")
        vote_counts[option] = int(count) if count is not None else 0
    
    return vote_counts

origins = [
    "http://10.25.156.34:31340", 
    "http://10.25.156.39:31340", 
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
