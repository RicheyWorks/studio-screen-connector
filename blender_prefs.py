# SPDX-License-Identifier: MIT
# studio-screen-connector / blender_prefs.py
# Run INSIDE Blender (4.x) to turn the start splash off for good:
#   blender -b --python blender_prefs.py            (user prefs are loaded; do NOT add --factory-startup)
# Sets preferences.view.show_splash = False and saves userpref.blend. Idempotent; prints one JSON line.
import json
import bpy

view = bpy.context.preferences.view
before = bool(view.show_splash)
view.show_splash = False
saved = False
err = None
try:
    bpy.ops.wm.save_userpref()
    saved = True
except Exception as e:  # background Blender without a writable config dir
    err = str(e)
print("BLENDER_PREFS " + json.dumps({"show_splash_before": before, "show_splash_now": bool(view.show_splash),
                                     "saved": saved, "error": err, "version": bpy.app.version_string}))
