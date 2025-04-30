# Nexia Cloud - AI Receptionist Microservices

Nexia is an AI-powered receptionist system that handles incoming and outgoing calls for businesses. This cloud-based implementation uses a microservices architecture to provide scalability and reliability.

## Services

- **whisper-service**: Transcribes audio using Groq or OpenAI API
- **tts-service**: Converts text to speech using edge-tts
- **ai-model-service**: Generates AI responses using Groq LLM
- **local-response-service**: Stores and retrieves cached responses
- **quick-response-service**: Generates fast bridging responses
- **central-service**: Orchestrates all services and manages conversations
- **twilio-handler**: Interfaces with Twilio for call handling

## Setup and Deployment

### Prerequisites

- Docker and Docker Compose
- Groq API key
- Twilio account with phone number
- MongoDB (provided as a Docker service)

### Environment Variables

Create a `.env` file with the following variables: