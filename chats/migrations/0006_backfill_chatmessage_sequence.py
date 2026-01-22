# chats/migrations/000X_backfill_chatmessage_sequence.py
from django.db import migrations


def backfill_sequence(apps, schema_editor):
    ChatMessage = apps.get_model("chats", "ChatMessage")

    # Iterate chat by chat
    chat_ids = (
        ChatMessage.objects
        .values_list("chat_id", flat=True)
        .distinct()
    )

    for chat_id in chat_ids:
        msgs = (
            ChatMessage.objects
            .filter(chat_id=chat_id)
            .order_by("created_at", "id")
        )

        for i, msg in enumerate(msgs, start=1):
            if msg.sequence != i:
                ChatMessage.objects.filter(pk=msg.pk).update(sequence=i)


class Migration(migrations.Migration):

    dependencies = [
        ("chats", "0005_alter_chatmessage_options_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_sequence, migrations.RunPython.noop),
    ]
