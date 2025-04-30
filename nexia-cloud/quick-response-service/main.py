from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import os
import logging
import random
import re
import pymongo
from pymongo import MongoClient
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("quick-response-service")

app = FastAPI(title="Nexia Quick Response Service")

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

# Default quick responses by conversation state
DEFAULT_RESPONSES = {
    0: ["I understand", "One moment", "Got it"],  # GREETING
    1: ["Checking schedule", "Let me see", "Looking at times"],  # SCHEDULING
    2: ["Checking", "Let me calculate", "One moment"],  # SERVICE_DETAILS
    3: ["Got it", "I see", "Checking that"],  # ADDRESS_CONFIRM
    4: ["Perfect", "Great", "Excellent"],  # APPOINTMENT_CONFIRM
    5: ["I understand", "I see", "Checking that"],  # ADD_SERVICES
    6: ["Thank you", "Thanks for calling", "Appreciate it"]  # FAREWELL
}

class QuickResponseRequest(BaseModel):
    customer_id: str
    user_text: str
    previous_ai: Optional[str] = ""
    flow_state: Optional[int] = 0

@app.post("/generate_quick_response")
async def generate_quick_response(request: QuickResponseRequest):
    """Generate a quick bridging response"""
    try:
        # Check for customer-specific responses first
        collection_name = f"quick_responses_{request.customer_id}"
        collection = db[collection_name]
        
        # Context-based features
        user_lower = request.user_text.lower()
        previous_ai_lower = request.previous_ai.lower() if request.previous_ai else ""
        
        # Extract useful context
        has_confirmation = any(word in user_lower for word in ["yes", "yeah", "ok", "sure", "right"])
        has_negation = any(word in user_lower for word in ["no", "not", "don't", "can't", "won't"])
        is_price_question = any(word in user_lower for word in ["cost", "price", "money", "dollars", "how much"])
        is_service_question = any(word in user_lower for word in ["service", "window", "clean", "washing"])
        is_schedule_question = any(word in user_lower for word in ["schedule", "time", "date", "when"])
        
        # Try to find a context-specific quick response
        context_match = None
        
        # Try exact query matching
        exact_match = collection.find_one({"user_text": user_lower})
        if exact_match:
            context_match = exact_match
        
        # Try context matching
        if not context_match:
            context_query = {
                "flow_state": request.flow_state
            }
            
            # Add context conditions
            if has_confirmation:
                context_query["has_confirmation"] = True
            if has_negation:
                context_query["has_negation"] = True
            if is_price_question:
                context_query["is_price_question"] = True
            if is_service_question:
                context_query["is_service_question"] = True
            if is_schedule_question:
                context_query["is_schedule_question"] = True
                
            context_match = collection.find_one(context_query)
            
        if context_match:
            # Update usage count
            collection.update_one(
                {"_id": context_match["_id"]},
                {"$inc": {"usage_count": 1}}
            )
            response = context_match["response"]
            logger.info(f"Found context-specific quick response: {response}")
        else:
            # Fall back to default responses by flow state
            flow_responses = DEFAULT_RESPONSES.get(request.flow_state, DEFAULT_RESPONSES[0])
            response = random.choice(flow_responses)
            logger.info(f"Using default quick response: {response}")
            
            # Add this to the database for future refinement
            collection.insert_one({
                "user_text": user_lower,
                "previous_ai": previous_ai_lower,
                "flow_state": request.flow_state,
                "has_confirmation": has_confirmation,
                "has_negation": has_negation,
                "is_price_question": is_price_question,
                "is_service_question": is_service_question,
                "is_schedule_question": is_schedule_question,
                "response": response,
                "created_at": time.time(),
                "usage_count": 1,
                "success_count": 0,
                "is_default": True
            })
            
        return {"response": response, "customer_id": request.customer_id}
    
    except Exception as e:
        logger.error(f"Error in generate_quick_response: {e}")
        # Fall back to a safe response
        return {"response": "One moment please", "customer_id": request.customer_id}

@app.post("/update_response_success")
async def update_response_success(customer_id: str, user_text: str, response: str, success: bool):
    """Update success metrics for a quick response"""
    try:
        collection_name = f"quick_responses_{customer_id}"
        collection = db[collection_name]
        
        user_lower = user_text.lower()
        
        # Try to find the response
        response_record = collection.find_one({
            "$or": [
                {"user_text": user_lower, "response": response},
                {"response": response, "is_default": True}
            ]
        })
        
        if response_record:
            # Update success metrics
            update_data = {"$inc": {"usage_count": 1}}
            if success:
                update_data["$inc"]["success_count"] = 1
                
            collection.update_one(
                {"_id": response_record["_id"]},
                update_data
            )
            logger.info(f"Updated response success metrics for customer {customer_id}")
            return {"status": "updated"}
        else:
            logger.warning(f"Response not found for customer {customer_id}: {response}")
            return {"status": "not_found"}
            
    except Exception as e:
        logger.error(f"Error in update_response_success: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/add_custom_response")
async def add_custom_response(
    customer_id: str, 
    user_pattern: str, 
    response: str, 
    flow_state: int,
    context_flags: Optional[Dict[str, bool]] = None
):
    """Add a custom quick response"""
    try:
        collection_name = f"quick_responses_{customer_id}"
        collection = db[collection_name]
        
        # Prepare context data
        context_data = {
            "flow_state": flow_state,
            "user_text": user_pattern.lower(),
            "response": response,
            "created_at": time.time(),
            "usage_count": 0,
            "success_count": 0,
            "is_default": False
        }
        
        # Add optional context flags
        if context_flags:
            context_data.update(context_flags)
            
        # Insert the new response
        collection.insert_one(context_data)
        
        logger.info(f"Added custom quick response for customer {customer_id}")
        return {"status": "added", "customer_id": customer_id}
        
    except Exception as e:
        logger.error(f"Error in add_custom_response: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "quick-response-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)