from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import edge_tts
import tempfile
import base64
import os
import logging
import asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("tts-service")

app = FastAPI(title="Nexia TTS Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TTSRequest(BaseModel):
    text: str
    voice: str = "en-US-AvaMultilingualNeural"
    rate: str = "+10%"
    customer_id: str = None

@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Synthesize speech using Edge TTS"""
    try:
        logger.info(f"TTS request for: '{request.text[:50]}...'")
        
        # Initialize Edge TTS
        tts = edge_tts.Communicate(
            text=request.text, 
            voice=request.voice,
            rate=request.rate
        )
        
        # Create a temporary file for the audio
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            temp_filename = temp_file.name
            
        # Save audio to the temporary file
        await tts.save(temp_filename)
        
        # Read the audio file and convert to base64
        with open(temp_filename, "rb") as f:
            audio_data = f.read()
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        
        # Clean up temporary file
        try:
            os.remove(temp_filename)
        except:
            pass
        
        return {
            "audio_base64": audio_base64,
            "text": request.text,
            "voice": request.voice
        }
        
    except Exception as e:
        logger.error(f"Error in synthesize_speech: {e}")
        raise HTTPException(status_code=500, detail=f"TTS error: {str(e)}")

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "tts-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)