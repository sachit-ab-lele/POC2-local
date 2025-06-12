from fastapi import FastAPI
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

client = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = client.voting
results_collection = db.results
poll_collection = db.polls

@app.get("/results")
def get_results():
    doc = results_collection.find_one(sort=[("_id", -1)])
    if doc:
        vote_counts = {k: v for k, v in doc.items() if not k.startswith('_')}
        return vote_counts
    else:
        active_poll = poll_collection.find_one({"is_active": True})
        if active_poll and active_poll.get("options"):
            return {option: 0 for option in active_poll.get("options")}
        return {}
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
