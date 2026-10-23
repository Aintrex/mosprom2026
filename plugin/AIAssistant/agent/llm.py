class ChatCompletionsClient(object):
    """Use urllib from the standard library for Python 3.5 compatibility.

    TODO: load URL/model/key from application Preferences, add timeouts,
    tool calling, image_url/base64, history, request limits and cancellation.
    Never expose the key to the browser or logs.
    """

    def complete(self, messages, tools):
        raise NotImplementedError("LLM integration is not implemented")
