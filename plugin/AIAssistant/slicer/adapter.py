PRINT_MODES = ("classic", "cylindrical", "cylindrical_full")
TEST_MACHINE_PROFILE = "Stereotech Hybrid 530 V5.2.4"


class SlicerAdapter(object):
    def __init__(self, app):
        self.app = app

    def schedule(self, callback, *args):
        # Callbacks run on the main Qt thread. Add a job/result bridge before
        # exposing actual operations to HTTP workers. Do not block Qt on HTTP.
        self.app.callLater(callback, *args)

    def get_setting(self, key, extruder=None):
        # TODO: implement using the global/extruder stack on the Qt thread.
        raise NotImplementedError("Slicer settings adapter is not implemented")
