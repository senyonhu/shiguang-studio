"""Errors presented consistently to the CLI and MCP client."""


class BridgeError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}
