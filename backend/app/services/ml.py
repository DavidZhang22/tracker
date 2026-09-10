from functools import lru_cache


# fake placeholder model
class DummyModel:
    def predict(self, text: str) -> dict:
        # Replace with real logic later
        return {
            "summary": text[:200],
            "score": len(text) % 100 / 100.0,
        }

@lru_cache(maxsize=1)
def get_model() -> DummyModel:
    # in real life, load from disk / checkpoint here
    return DummyModel()

def predict_from_text(text: str) -> dict:
    model = get_model()
    return model.predict(text)
