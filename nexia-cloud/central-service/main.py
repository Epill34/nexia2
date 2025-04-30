from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import os
import logging
import json
import httpx
import asyncio
import base64
import time
from datetime import datetime
from pymongo import MongoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("central-service")

app = FastAPI(title="Nexia Central Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Service URLs from environment
WHISPER_SERVICE_URL = os.environ.get("WHISPER_SERVICE_URL", "http://whisper-service:8000")
TTS_SERVICE_URL = os.environ.get("TTS_SERVICE_URL", "http://tts-service:8000")
AI_MODEL_SERVICE_URL = os.environ.get("AI_MODEL_SERVICE_URL", "http://ai-model-service:8000") 
LOCAL_RESPONSE_SERVICE_URL = os.environ.get("LOCAL_RESPONSE_SERVICE_URL", "http://local-response-service:8000")
QUICK_RESPONSE_SERVICE_URL = os.environ.get("QUICK_RESPONSE_SERVICE_URL", "http://quick-response-service:8000")

# MongoDB setup
mongodb_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")
client = MongoClient(mongodb_uri)
db = client["nexia_db"]

# Active conversations
active_conversations = {}

# WebSocket connections for each customer
websocket_connections = {}

class AudioRequest(BaseModel):
    audio_base64: str
    customer_id: str
    conversation_id: Optional[str] = None
    call_id: Optional[str] = None

class InitializeRequest(BaseModel):
    customer_id: str
    business_name: str
    services: List[str]
    system_prompt: Optional[str] = None

class ConversationState:
    def __init__(self, customer_id: str):
        self.customer_id = customer_id
        self.conversation_history = []
        self.flow_state = 0  # GREETING state by default
        self.last_activity = time.time()
        self.appointment_details = {}
        self.current_customer = None
        self.is_confirmation_pending = False
        self.schedule_checking = False
        self.notes = []
        self.confirmations = []
        self.previous_ai_response = ""
        self.previous_user_response = ""
        self.conversation_id = f"{customer_id}_{int(time.time())}"
        self.call_id = None
        
    def to_dict(self):
        return {
            "customer_id": self.customer_id,
            "conversation_id": self.conversation_id,
            "call_id": self.call_id,
            "flow_state": self.flow_state,
            "last_activity": self.last_activity,
            "appointment_details": self.appointment_details,
            "current_customer": self.current_customer,
            "is_confirmation_pending": self.is_confirmation_pending,
            "schedule_checking": self.schedule_checking,
            "notes": self.notes,
            "confirmations": self.confirmations,
            "previous_ai_response": self.previous_ai_response,
            "previous_user_response": self.previous_user_response
        }

@app.post("/initialize")
async def initialize_customer(request: InitializeRequest):
    """Initialize a new customer with business details"""
    try:
        customer_id = request.customer_id
        
        # Set up system prompt for the customer
        async with httpx.AsyncClient() as client:
            if request.system_prompt:
                await client.post(
                    f"{AI_MODEL_SERVICE_URL}/set_system_prompt",
                    params={"customer_id": customer_id, "system_prompt": request.system_prompt}
                )
            else:
                # Create default system prompt with business name and services
                services_text = "\n".join([f"- {service}" for service in request.services])
                default_prompt = f"""SYSTEM PROMPT FOR NEXIA AI RECEPTIONIST

1. CORE IDENTITY & BASICS
------------------------
Role: Nexia - Advanced AI receptionist for {request.business_name}
Languages: English/Spanish and others
Response Format: 
- One short sentence only
- Must end with a question
- Natural conversational tone (phone call style, not email)

2. BUSINESS SERVICES
------------------
{services_text}

3. CRITICAL REQUIREMENTS
----------------------
ALWAYS get all customer information
ALWAYS attempt at least two upsells
ALWAYS include COMPLETE DETAILS in [CONFIRMED] tag
NEVER end call until all details are confirmed
ALWAYS ask if customer needs anything else before ending call
Use ONE SENTENCE responses ending with questions
"""
                await client.post(
                    f"{AI_MODEL_SERVICE_URL}/set_system_prompt",
                    params={"customer_id": customer_id, "system_prompt": default_prompt}
                )
                
        # Create collections for this customer
        db[f"conversations_{customer_id}"]
        db[f"local_responses_{customer_id}"]
        db[f"quick_responses_{customer_id}"]
        db[f"customers_{customer_id}"]
        
        # Store business info
        db["business_info"].update_one(
            {"customer_id": customer_id},
            {
                "$set": {
                    "business_name": request.business_name,
                    "services": request.services,
                    "created_at": datetime.now(),
                    "last_updated": datetime.now()
                }
            },
            upsert=True
        )
        
        logger.info(f"Initialized new customer: {customer_id} ({request.business_name})")
        
        return {
            "status": "initialized",
            "customer_id": customer_id,
            "business_name": request.business_name
        }
        
    except Exception as e:
        logger.error(f"Error in initialize_customer: {e}")
        raise HTTPException(status_code=500, detail=f"Initialization error: {str(e)}")

@app.post("/process_audio")
async def process_audio(request: AudioRequest):
    """Process audio from a call"""
    customer_id = request.customer_id
    
    # Get or create conversation state
    if request.conversation_id and request.conversation_id in active_conversations:
        state = active_conversations[request.conversation_id]
    else:
        state = ConversationState(customer_id)
        if request.conversation_id:
            state.conversation_id = request.conversation_id
        if request.call_id:
            state.call_id = request.call_id
        active_conversations[state.conversation_id] = state
    
    # Update activity timestamp
    state.last_activity = time.time()
    
    try:
        # Step 1: Transcribe audio with Whisper
        async with httpx.AsyncClient(timeout=30.0) as client:
            transcribe_response = await client.post(
                f"{WHISPER_SERVICE_URL}/transcribe",
                json={"audio_base64": request.audio_base64, "customer_id": customer_id}
            )
            
            if transcribe_response.status_code != 200:
                raise HTTPException(status_code=500, detail="Speech recognition failed")
                
            user_text = transcribe_response.json()["text"]
            
            if not user_text:
                return {"status": "no_speech_detected"}
                
            logger.info(f"Transcribed: '{user_text}'")
            
            # Update state with user text
            state.previous_user_response = user_text
            
            # Record in conversation history
            state.conversation_history.append({
                "role": "user",
                "content": user_text,
                "timestamp": time.time()
            })
            
            # Step 2: Try to get a quick response
            quick_response = await client.post(
                f"{QUICK_RESPONSE_SERVICE_URL}/generate_quick_response",
                json={
                    "customer_id": customer_id,
                    "user_text": user_text,
                    "previous_ai": state.previous_ai_response,
                    "flow_state": state.flow_state
                }
            )
            
            quick_response_text = None
            if quick_response.status_code == 200:
                quick_response_text = quick_response.json()["response"]
                
                # Generate audio for quick response
                tts_response = await client.post(
                    f"{TTS_SERVICE_URL}/synthesize",
                    json={
                        "text": quick_response_text,
                        "customer_id": customer_id
                    }
                )
                
                if tts_response.status_code == 200:
                    # Record quick response in history
                    state.conversation_history.append({
                        "role": "assistant",
                        "content": quick_response_text,
                        "timestamp": time.time(),
                        "type": "quick_response"
                    })
                    
                    # Send quick response to websocket if connected
                    if state.conversation_id in websocket_connections:
                        await websocket_connections[state.conversation_id].send_text(
                            json.dumps({
                                "type": "quick_response",
                                "text": quick_response_text,
                                "audio_base64": tts_response.json()["audio_base64"]
                            })
                        )
            
            # Step 3: Check for local response match
            local_response_check = await client.post(
                f"{LOCAL_RESPONSE_SERVICE_URL}/check_local_response",
                json={
                    "customer_id": customer_id,
                    "lair": state.previous_ai_response,
                    "lurs": user_text
                }
            )
            
            if local_response_check.status_code == 200 and local_response_check.json()["found"]:
                local_response_text = local_response_check.json()["response"]
                
                # Generate audio for local response
                tts_response = await client.post(
                    f"{TTS_SERVICE_URL}/synthesize",
                    json={
                        "text": local_response_text,
                        "customer_id": customer_id
                    }
                )
                
                if tts_response.status_code == 200:
                    # Update state
                    state.previous_ai_response = local_response_text
                    
                    # Record local response in history
                    state.conversation_history.append({
                        "role": "assistant",
                        "content": local_response_text,
                        "timestamp": time.time(),
                        "type": "local_response"
                    })
                    
                    # Send local response to websocket if connected
                    if state.conversation_id in websocket_connections:
                        await websocket_connections[state.conversation_id].send_text(
                            json.dumps({
                                "type": "local_response",
                                "text": local_response_text,
                                "audio_base64": tts_response.json()["audio_base64"]
                            })
                        )
                    
                    return {
                        "type": "local_response",
                        "text": local_response_text,
                        "audio_base64": tts_response.json()["audio_base64"],
                        "conversation_id": state.conversation_id
                    }
            
            # Step 4: Get business info for context
            business_info = db["business_info"].find_one({"customer_id": customer_id})
            
            # Step 5: Call the main AI model
            ai_response = await client.post(
                f"{AI_MODEL_SERVICE_URL}/generate_response",
                json={
                    "customer_id": customer_id,
                    "user_text": user_text,
                    "conversation_history": state.conversation_history[-20:] if state.conversation_history else [],
                    "customer_data": {
                        "business_name": business_info.get("business_name") if business_info else None,
                        "services": business_info.get("services") if business_info else None
                    }
                }
            )
            
            if ai_response.status_code != 200:
                raise HTTPException(status_code=500, detail="AI model service failed")
                
            ai_response_json = ai_response.json()
            ai_response_text = ai_response_json["response"]
            brackets = ai_response_json.get("brackets", {})
            
            # Update flow state based on AI response
            ai_lower = ai_response_text.lower()
            
            if '[CALENDAR]' in ai_response_text and '[SCHEDULE]' in ai_response_text:
                state.flow_state = 1  # SCHEDULING
                state.schedule_checking = True
            elif any(word in ai_lower for word in ["address", "location"]) and "confirm" in ai_lower:
                state.flow_state = 3  # ADDRESS_CONFIRM
            elif any(word in ai_lower for word in ["service", "window", "cleaning"]):
                state.flow_state = 2  # SERVICE_DETAILS
            elif "confirm" in ai_lower and any(word in ai_lower for word in ["appointment", "scheduled"]):
                state.flow_state = 4  # APPOINTMENT_CONFIRM
            elif any(phrase in ai_lower for phrase in ["would you like to add", "additional service"]):
                state.flow_state = 5  # ADD_SERVICES
            elif '[call_ended]' in ai_response_text:
                state.flow_state = 6  # FAREWELL
            
            # Handle appointment details
            if "appointment_details" in brackets and brackets["appointment_details"]:
                state.appointment_details.update(brackets["appointment_details"])
                state.appointment_details["timestamp"] = time.time()
            
            # Handle confirmation
            if brackets.get("confirmed", False):
                state.is_confirmation_pending = False
                
                # Save appointment to database
                db[f"appointments_{customer_id}"].insert_one({
                    "date": state.appointment_details.get("date"),
                    "time": state.appointment_details.get("time"),
                    "services": state.appointment_details.get("services", []),
                    "customer_name": state.current_customer,
                    "created_at": datetime.now(),
                    "conversation_id": state.conversation_id,
                    "call_id": state.call_id
                })
            
            # Handle notes
            if "notes" in brackets and brackets["notes"]:
                state.notes.extend(brackets["notes"])
            
            # Handle call ending
            call_ended = brackets.get("call_ended", False)
            
            # Step 6: Save this as a local response for next time
            await client.post(
                f"{LOCAL_RESPONSE_SERVICE_URL}/save_local_response",
                json={
                    "customer_id": customer_id,
                    "lair": state.previous_ai_response,
                    "lurs": user_text,
                    "lr": ai_response_text
                }
            )
            
            # Update state
            state.previous_ai_response = ai_response_text
            
            # Record AI response in history
            state.conversation_history.append({
                "role": "assistant",
                "content": ai_response_text,
                "timestamp": time.time(),
                "type": "ai_response"
            })
            
            # Generate TTS for AI response
            tts_response = await client.post(
                f"{TTS_SERVICE_URL}/synthesize",
                json={
                    "text": ai_response_text,
                    "customer_id": customer_id
                }
            )
            
            if tts_response.status_code != 200:
                raise HTTPException(status_code=500, detail="TTS service failed")
            
            audio_base64 = tts_response.json()["audio_base64"]
            
            # Send AI response to websocket if connected
            if state.conversation_id in websocket_connections:
                await websocket_connections[state.conversation_id].send_text(
                    json.dumps({
                        "type": "ai_response",
                        "text": ai_response_text,
                        "audio_base64": audio_base64
                    })
                )
            
            # Update usage metrics
            if quick_response_text:
                await client.post(
                    f"{QUICK_RESPONSE_SERVICE_URL}/update_response_success",
                    json={
                        "customer_id": customer_id,
                        "user_text": user_text,
                        "response": quick_response_text,
                        "success": True  # We didn't interrupt it
                    }
                )
            
            # Save the complete conversation if the call is ending
            if call_ended:
                # Archive the conversation
                db[f"completed_conversations_{customer_id}"].insert_one({
                    "conversation_id": state.conversation_id,
                    "call_id": state.call_id,
                    "customer_id": customer_id,
                    "start_time": state.conversation_history[0]["timestamp"] if state.conversation_history else time.time(),
                    "end_time": time.time(),
                    "flow_states": [state.flow_state],
                    "appointment_details": state.appointment_details,
                    "notes": state.notes,
                    "conversation": state.conversation_history,
                    "completed": True
                })
                
                # Remove from active conversations
                if state.conversation_id in active_conversations:
                    del active_conversations[state.conversation_id]
                
                # Close websocket if open
                if state.conversation_id in websocket_connections:
                    await websocket_connections[state.conversation_id].close()
                    del websocket_connections[state.conversation_id]
            
            return {
                "type": "ai_response",
                "text": ai_response_text,
                "audio_base64": audio_base64,
                "conversation_id": state.conversation_id,
                "flow_state": state.flow_state,
                "call_ended": call_ended
            }
    
    except Exception as e:
        logger.error(f"Error in process_audio: {e}")
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

@app.websocket("/ws/{conversation_id}")
async def websocket_endpoint(websocket: WebSocket, conversation_id: str):
    """WebSocket connection for real-time conversation updates"""
    await websocket.accept()
    
    # Store connection
    websocket_connections[conversation_id] = websocket
    
    try:
        # Send initial state if conversation exists
        if conversation_id in active_conversations:
            state = active_conversations[conversation_id]
            await websocket.send_text(
                json.dumps({
                    "type": "state_update",
                    "state": state.to_dict(),
                    "history": state.conversation_history[-10:] if state.conversation_history else []
                })
            )
        
        # Wait for disconnect
        while True:
            data = await websocket.receive_text()
            # Process any commands from client
            try:
                message = json.loads(data)
                if message.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except:
                pass
    
    except WebSocketDisconnect:
        # Remove connection on disconnect
        if conversation_id in websocket_connections:
            del websocket_connections[conversation_id]
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if conversation_id in websocket_connections:
            del websocket_connections[conversation_id]

# Background task to clean up inactive conversations
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_inactive_conversations())

async def cleanup_inactive_conversations():
    """Clean up inactive conversations"""
    while True:
        current_time = time.time()
        to_remove = []
        
        for conv_id, state in active_conversations.items():
            # If inactive for more than 30 minutes
            if current_time - state.last_activity > 1800:
                to_remove.append(conv_id)
                
                # Archive the conversation
                db[f"completed_conversations_{state.customer_id}"].insert_one({
                    "conversation_id": state.conversation_id,
                    "call_id": state.call_id,
                    "customer_id": state.customer_id,
                    "start_time": state.conversation_history[0]["timestamp"] if state.conversation_history else time.time(),
                    "end_time": time.time(),
                    "flow_states": [state.flow_state],
                    "appointment_details": state.appointment_details,
                    "notes": state.notes,
                    "conversation": state.conversation_history,
                    "completed": False,
                    "reason": "inactivity_timeout"
                })
                
                # Close websocket if open
                if conv_id in websocket_connections:
                    try:
                        await websocket_connections[conv_id].close()
                    except:
                        pass
                    del websocket_connections[conv_id]
        
        # Remove inactive conversations
        for conv_id in to_remove:
            logger.info(f"Removing inactive conversation: {conv_id}")
            del active_conversations[conv_id]
        
        await asyncio.sleep(60)  # Check every minute

# Health check endpoint
@app.get("/health")
def health_check():
    return {
        "status": "ok", 
        "service": "central-service",
        "active_conversations": len(active_conversations),
        "active_websockets": len(websocket_connections)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)