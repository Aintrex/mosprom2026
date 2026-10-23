# Lazy import lets the development server run without UM or PyQt5.
def getMetaData():
    return {}


def register(app):
    from .AIAssistant import AIAssistant
    return {"extension": AIAssistant(app)}
