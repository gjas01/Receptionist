# Front Desk

Virtual receptionist for a Twilio phone line. Twilio is the carrier only. Conversation audio is handled by **qwen3.8-omni-flash-realtime**, including one-shot voice cloning.

Sprint 1 covers account setup, Twilio credential storage, voice cloning, a local headphone test call, and the Twilio media-stream webhook.

## Local Docker test

This container does not have a public IP, so Twilio cannot reach the webhook. Use **Test Mode** and this computer’s microphone and speakers.

1. Copy the environment file and add your Qwen key:

   ```bash
   cp .env.example .env
   ```

2. Set at least:

   ```
   API_ENDPOINT=https://maas.qwencloudapi.com
   DASHSCOPE_KEY=your-key
   SECRET_KEY=a-long-random-string
   PUBLIC_BASE_URL=http://localhost:8000
   QWEN_MODEL=qwen3.8-omni-flash-realtime
   ```

   `API_ENDPOINT` is the Qwen/DashScope host (no path). Lightsail or Alibaba Model Studio workspace hosts also work, for example `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`.

3. Build and run:

   ```bash
   docker compose up --build
   ```

4. Open [http://localhost:8000](http://localhost:8000).

5. Register, add a phone number if you have Twilio credentials (webhook registration will fail locally and that is expected), clone a voice, switch to **Test Mode**, then simulate a call.

## Voice cloning

Cloning follows the [Qwen voice cloning guide](https://docs.qwencloud.com/developer-guides/speech/voice-cloning):

- Enrollment model: `qwen-voice-enrollment`
- Target model: `qwen3.8-omni-flash-realtime` (must match the realtime conversation model)
- `voice_clone_mode`: `normal` (required for Qwen 3.8)
- Playback uses the same realtime model with `session.audio.output.voice` set to the returned voice id

After cloning, the app plays:

> Hi \<name\>, This is your cloned voice. This is how your phone will be answered in future.

## Incoming call greeting

UK time of day:

- After midnight until noon: morning
- Noon until 17:00: afternoon
- 17:00 until midnight: evening

Example: `Good evening, this is Acme Ltd, how can I help?`

If no business name is set, the greeting uses `\<name\>'s office`.

## Amazon Lightsail

1. Put the project on the instance and create `.env` with a public HTTPS URL:

   ```
   PUBLIC_BASE_URL=https://your-domain-or-static-ip
   ```

2. Open ports 80/443 (and 8000 if you expose Uvicorn directly). Twilio voice and media streams require a public `https` / `wss` URL.

3. Run `docker compose up -d --build`.

4. Save the Twilio number in the app so the voice webhook is registered to `/webhook/voice`. That endpoint returns TwiML that streams the call to `/ws/twilio`, which bridges 8 kHz mulaw audio to Qwen 16 kHz / 24 kHz PCM.

Place a reverse proxy with TLS in front of port 8000 for production.
