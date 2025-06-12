from fastapi import FastAPI
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

# MongoDB client
# Initialize MongoDB client globally, connecting to the 'mongo' service on its internal port 27017
client = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = client.voting
results_collection = db.results # For vote snapshots
poll_collection = db.polls      # For poll definitions (read-only access here)

@app.get("/results")
def get_results():
    # Get most recent snapshot
    doc = results_collection.find_one(sort=[("_id", -1)])
    if doc:
        # Filter out metadata keys, returning only vote counts
        vote_counts = {k: v for k, v in doc.items() if not k.startswith('_')}
        return vote_counts
    else:
        # If no snapshots, try to return 0 counts for options of the current active poll
        active_poll = poll_collection.find_one({"is_active": True})
        if active_poll and active_poll.get("options"):
            return {option: 0 for option in active_poll.get("options")}
        return {} # No active poll or no snapshots
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
