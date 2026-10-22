from telethon.sessions import StringSession
from telethon.sync import TelegramClient

print("Get API_ID / API_HASH from https://my.telegram.org")
api_id = int(input("API_ID: ").strip())
api_hash = input("API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session = client.session.save()
    print("\nSESSION_STRING (copy it into your host's secret/env settings):\n")
    print(session)
    print("\nDo not paste it in chats, screenshots or git.")
    if input("Also save a copy to your Saved Messages? [y/N] ").strip().lower() == "y":
        client.send_message("me", f"`{session}`")
        print("Saved. Delete that message once you've stored the string safely.")
