import os

_HERE = os.path.dirname(os.path.abspath(__file__))


class Config:
    def __init__(self, dotenv_path=os.path.join(_HERE, ".env")):
        self.env = {}
        if os.path.exists(dotenv_path):
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        self.env[k.strip()] = v.strip()
        
    def get(self, key, default=None):
        return self.env.get(key, os.environ.get(key, default))

# Load standard config
config = Config()
