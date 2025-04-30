from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import os
import logging
import pymongo
from pymongo import MongoClient
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("local-response-service")

app = FastAPI(title="Nexia Local Response Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up MongoDB connection
mongodb_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")
client = MongoClient(mongodb_uri)
db = client["nexia_db"]

class ResponsePair(BaseModel):
    lair: str  # Last AI Response
    lurs: str  # Last User Response
    lr: str    # Local Response
    customer_id: str

class CheckResponseRequest(BaseModel):
    customer_id: str
    lair: str
    lurs: str

@app.post("/check_local_response")
async def check_local_response(request: CheckResponseRequest):
    """Check if there's a matching local response for the given context"""
    try:
        # Get the customer's collection (or create if not exists)
        collection_name = f"local_responses_{request.customer_id}"
        collection = db[collection_name]
        
        # Look for an exact match
        match = collection.find_one({
            "lair": request.lair,
            "lurs": request.lurs
        })
        
        # Clean up by removing multiple spaces and standardizing case
        if not match:
            cleaned_lair = " ".join(request.lair.lower().split())
            cleaned_lurs = " ".join(request.lurs.lower().split())
            
            match = collection.find_one({
                "lair_clean": cleaned_lair,
                "lurs_clean": cleaned_lurs
            })
        
        if match:
            logger.info(f"Found local response match for customer {request.customer_id}")
            # Update usage count and last used timestamp
            collection.update_one(
                {"_id": match["_id"]},
                {
                    "$inc": {"usage_count": 1},
                    "$set": {"last_used": time.time()}
                }
            )
            return {"found": True, "response": match["lr"]}
        else:
            logger.info(f"No local response match for customer {request.customer_id}")
            return {"found": False}
    
    except Exception as e:
        logger.error(f"Error in check_local_response: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/save_local_response")
async def save_local_response(pair: ResponsePair):
    """Save a new conversation pair"""
    try:
        # Get the customer's collection (or create if not exists)
        collection_name = f"local_responses_{pair.customer_id}"
        collection = db[collection_name]
        
        # Create cleaned versions for better matching
        lair_clean = " ".join(pair.lair.lower().split())
        lurs_clean = " ".join(pair.lurs.lower().split())
        
        # Check for existing entry first
        existing = collection.find_one({
            "lair": pair.lair,
            "lurs": pair.lurs
        })
        
        if existing:
            # Update existing entry
            collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "lr": pair.lr,
                        "last_updated": time.time()
                    },
                    "$inc": {"update_count": 1}
                }
            )
            logger.info(f"Updated existing response for customer {pair.customer_id}")
        else:
            # Store the new pair
            collection.insert_one({
                "lair": pair.lair,
                "lurs": pair.lurs,
                "lr": pair.lr,
                "lair_clean": lair_clean,
                "lurs_clean": lurs_clean,
                "created_at": time.time(),
                "last_updated": time.time(),
                "last_used": None,
                "usage_count": 0,
                "update_count": 0
            })
            logger.info(f"Saved new local response for customer {pair.customer_id}")
        
        return {"status": "saved", "customer_id": pair.customer_id}
        
    except Exception as e:
        logger.error(f"Error in save_local_response: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/get_responses/{customer_id}")
async def get_responses(customer_id: str, limit: int = 100):
    """Get all responses for a customer"""
    try:
        collection_name = f"local_responses_{customer_id}"
        collection = db[collection_name]
        
        # Get responses sorted by usage count (most used first)
        responses = list(collection.find(
            {},
            {"_id": 0}  # Exclude MongoDB _id
        ).sort("usage_count", pymongo.DESCENDING).limit(limit))
        
        return {"responses": responses, "count": len(responses)}
        
    except Exception as e:
        logger.error(f"Error in get_responses: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "local-response-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)