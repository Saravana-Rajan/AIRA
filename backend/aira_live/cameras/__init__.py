"""Shop cameras (CCTV / IP camera over RTSP, or a second phone on a stand). OFF by default.

The demo uses the chest-mounted phone only. This package adds an optional, configurable set of fixed cameras
that watch the shop and tell the owner what matters, in his language:

- ``config``   the ``cameras`` list in the Firestore ``config/runtime`` doc (``remote_config.cameras()``);
               an empty list (the default) means nothing in here runs.
- ``sources``  where frames come from: ``PhoneSource`` (a phone in shop-camera mode on ``/ws``) or
               ``RtspSource`` (an ffmpeg subprocess; the URL lives only in env ``CAMERA_<NAME>_URL``).
- ``motion``   a cheap motion gate so only changed frames, at most N per minute, reach Gemini.
- ``watch``    the per-camera loop and the four modes: delivery, shelf, counter, security.

Rules that apply here as everywhere: the model proposes, the backend commits. A camera only ever produces an
``alerts`` event and a short spoken notice. It never writes stock, money or state, and it never invents a count.
"""
