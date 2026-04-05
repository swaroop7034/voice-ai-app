# Voice AI App

AI voice assistant monorepo with:

- Mobile app: Expo + React Native (mobile-app/ARIES_V2)
- Backend: FastAPI + local AI services (server-backend)
- Calendar + memory integrations: Google Calendar + Supabase
- Voice-auth safe folder flow: keyword + speaker verification

## Current Features

### 1) Voice Chat Flow

1. Mobile records voice and uploads audio to backend
2. Backend converts speech to text (Whisper STT)
3. Backend runs assistant logic (calendar/task intent + LLM path)
4. Backend converts response to speech (TTS)
5. Mobile plays returned audio

### 2) Personalization + Memory

- Supabase interaction storage
- Vector retrieval for memory context
- User profile updates in background
- Behavior analysis and proactive suggestions

### 3) Calendar Intelligence

- Google Calendar read/create/reschedule utilities
- Upcoming-event monitoring via proactive watcher
- Best-effort event sync into Supabase

### 4) Safe Folder Voice Authentication

- Voice command keyword: safe folder
- No LLM usage for this command path
- Backend verifies voice sample against stored embedding
- First valid access can auto-enroll voice embedding
- Access granted triggers Safe Folder UI on mobile

## Repository Structure

voice-ai-app/
- mobile-app/
    - ARIES_V2/                  Expo app (frontend)
- server-backend/
    - main.py                    FastAPI app and routes
    - core/                      Brain, STT/TTS, voice auth, behavior modules
    - integrations/              Supabase and schema helpers
    - tools/                     Calendar and search modules
    - data/                      Google credentials/token (local only)
    - temp_audio/                Runtime audio cache (local only)
- README.md
- .gitignore

## API Endpoints

### POST /chat

- Input: multipart audio file
- Purpose: main voice chat
- Special behavior: if transcript equals safe folder, backend runs voice-auth flow directly (no LLM)

Success shape (normal chat):
- status: success
- user_text
- aries_text
- audio (base64)

Safe-folder success shape:
- status: success
- access_granted: true
- message: Access granted

Safe-folder enrolled shape:
- status: enrolled
- message: Voice registered successfully

### POST /text-input

- Input: JSON with text and optional user_id
- Purpose: text route used by frontend slot interactions

### GET /check-alerts

- Purpose: heartbeat for proactive alerts and suggestions

### POST /safe-folder/enroll

- Input: multipart audio
- Purpose: explicit voice enrollment endpoint

### POST /safe-folder/access

- Input: multipart audio + optional user_id
- Purpose: keyword + speaker verification endpoint

## Safe Folder Auth Logic

1. Backend runs Whisper transcript on sample
2. Transcript must equal safe folder (case-insensitive)
3. Stored embedding is fetched from Supabase voice_auth table
4. If no embedding exists, backend enrolls using current sample
5. If embedding exists, cosine similarity is computed
6. Access granted when similarity > VOICE_AUTH_THRESHOLD (default 0.96)

## Supabase Requirements

Minimum table used by voice auth:

create table if not exists public.voice_auth (
    id bigserial primary key,
    user_id text not null unique,
    embedding jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists voice_auth_user_id_idx
    on public.voice_auth (user_id);

## Environment Variables

Set in server-backend/.env:

- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
- SUPABASE_VOICE_AUTH_TABLE (optional, default: voice_auth)
- VOICE_AUTH_THRESHOLD (optional, default: 0.96)
- SUPABASE_DEFAULT_USER_ID (optional fallback user id)

Also required locally for calendar integration:

- server-backend/data/credentials.json (Google OAuth client)
- server-backend/data/token.json (generated after auth)

## How To Run

### 1) Backend

From repository root:

1. Create and activate virtual environment
2. Install dependencies:
     pip install -r server-backend/requirements.txt
3. Ensure .env and Google credentials are present
4. Start backend:
     cd server-backend
     python main.py

Default server URL: http://0.0.0.0:8000

### 2) Mobile App

From repository root:

1. Install dependencies:
     cd mobile-app/ARIES_V2
     npm install
2. Start Expo:
     npx expo start
3. Ensure mobile app can reach backend host IP

Optional: define EXPO_PUBLIC_API_BASE_URL in mobile environment if backend URL changes.

## Key Frontend Screens

- Main voice screen: mobile-app/ARIES_V2/app/(tabs)/index.tsx
- Safe folder access screen: mobile-app/ARIES_V2/app/safe-folder.tsx
- Safe folder content screen: mobile-app/ARIES_V2/app/safe-folder-screen.tsx

## Troubleshooting

### Safe folder says Access granted but screen does not open

- Confirm mobile receives either access_granted: true or text containing Access granted for safe folder intent
- Confirm Expo Router route exists: /safe-folder-screen
- Confirm frontend is running latest bundle after code changes

### Voice mismatch always happens

- Check Supabase voice_auth row exists for correct Gmail user_id
- Re-enroll with clearer audio sample in quiet environment
- Verify VOICE_AUTH_THRESHOLD is not overly strict

### Backend not reachable from mobile

- Use machine LAN IP instead of localhost in mobile config
- Ensure firewall allows inbound port 8000

## Team Ownership (Suggested)

- Member 1: mobile UI/UX and voice interaction frontend
- Member 2: STT/TTS pipelines and audio quality
- Member 3: intelligence logic and LLM behavior
- Member 4: API orchestration, integrations, and release coordination

## Notes

- Keep secrets out of git (see .gitignore)
- temp_audio is runtime-only data
- Supabase schema and behavior modules are designed for iterative expansion