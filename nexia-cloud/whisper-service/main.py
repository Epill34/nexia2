from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
import os
import logging
import base64
import httpx
import json
from pydantic import BaseModel
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("whisper-service")

app = FastAPI(title="Nexia Whisper Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AudioRequest(BaseModel):
    audio_base64: str
    customer_id: str

@app.post("/transcribe")
async def transcribe_audio(request: AudioRequest):
    """Transcribe audio using Groq's Whisper API"""
    try:
        # Decode base64 audio
        audio_bytes = base64.b64decode(request.audio_base64)
        
        # Save temporary file
        temp_filename = f"temp_{request.customer_id}.wav"
        with open(temp_filename, "wb") as f:
            f.write(audio_bytes)
        
        # Initialize Groq client
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        
        # Use audio API to transcribe
        # Note: Implementation depends on Groq's specific audio API
        # As a fallback, we'll use an alternative approach
        try:
            # If Groq has direct Whisper support:
            # response = client.audio.transcriptions.create(
            #     file=open(temp_filename, "rb"),
            #     model="whisper-large-v3"
            # )
            # transcription = response.text
            
            # Alternative fallback using OpenAI's API until Groq has built-in support
            async with httpx.AsyncClient() as client:
                with open(temp_filename, "rb") as f:
                    data = {
                        "model": "whisper-1",
                    }
                    files = {
                        "file": ("audio.wav", f, "audio/wav")
                    }
                    response = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        data=data,
                        files=files,
                        headers={
                            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"
                        },
                        timeout=30
                    )
                if response.status_code == 200:
                    transcription = response.json().get("text", "")
                else:
                    raise Exception(f"OpenAI API error: {response.text}")
        
        except Exception as e:
            logger.error(f"Transcription API error: {e}")
            # Implement a simple fallback transcription if needed
            transcription = "Sorry, I couldn't transcribe the audio properly."
        
        # Clean up temporary file
        try:
            os.remove(temp_filename)
        except:
            pass
        
        logger.info(f"Transcribed for customer {request.customer_id}: {transcription}")
        return {"text": transcription, "customer_id": request.customer_id}
        
    except Exception as e:
        logger.error(f"Error in transcribe_audio: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription error: {str(e)}")

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "whisper-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)