from __future__ import annotations

from google.cloud.firestore import Client

from pybot.domain.model.types import BotConfig
from pybot.infra.config.schema import parse_config


class FirestoreConfigRepository:
    def __init__(self, firestore: Client):
        self.firestore = firestore

    def get_current_config(self) -> BotConfig:
        snapshot = self.firestore.document("config/current").get()
        if not snapshot.exists:
            raise RuntimeError("config/current document is missing")
        data = snapshot.to_dict()
        return parse_config(data)

