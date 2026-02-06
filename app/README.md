# Discord Bot (mcserver-aws)

This bot calls the `mc-control` Lambda Function URL so you can control worlds from Discord.

## Setup

- Create a `.env` (see `.env.example`)
- Install deps:
  - `python -m pip install -r requirements.txt`
- Run:
  - `python main.py`

Required env vars:
- `DISCORD_TOKEN`
- `MC_CONTROL_URL`

Optional:
- `MC_CONTROL_TOKEN`
- `GUILD_ID` (sync commands immediately to a single guild)

## Commands

- `/mc start [world]`
- `/mc stop [world]`
- `/mc status [world]`
- `/mc snapshot [world]`

## Notes

- If `GUILD_ID` is set, commands are synced to that guild immediately.
- If `GUILD_ID` is not set, global sync is used and can take up to ~1 hour to appear.
- You can restrict usage via `ALLOWED_ROLE_IDS` / `ALLOWED_USER_IDS`.
