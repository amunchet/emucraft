"""
This is borrowed from the faro code from Facebook

I could never get this way to work
"""
import os
import time
import threading
import queue

os.environ["WEBRTC_IP"] = "0.0.0.0"
os.environ["WEBRTC_PORT"] = "8889"

import open3d as o3d
o3d.visualization.webrtc_server.enable_webrtc()
from open3d.visualization import O3DVisualizer, gui

class O3dViz(threading.Thread):
    def __init__(self, *args, **kwargs):
        self.q = queue.Queue()
        self._id = 0
        super().__init__(*args, **kwargs)

    def put(self, obj):
        self.q.put(obj)

    def run(self):
        app = gui.Application.instance

        app.initialize()
        w = O3DVisualizer("o3dviz", 1024, 768)
        w.set_background((0.0, 0.0, 0.0, 1.0), None)

        app.add_window(w)
        reset_camera = False

        while True:
            app.run_one_tick()
            time.sleep(0.001)

            try:
                geometry = self.q.get_nowait()

                # remove previous object
                if self._id > 0:
                    w.remove_geometry("objid_{}".format(self._id - 1))

                # render new object
                w.add_geometry("objid_{}".format(self._id), geometry)
                if not reset_camera:
                    w.reset_camera_to_default()
                    reset_camera = True
                self._id = self._id + 1
            except queue.Empty:
                pass


o3dviz = O3dViz()
o3dviz.start()

while True:
    x, y, z = 0.1, 0.0, 0.0

    while True:
        # make a box translate over x
        x = x + 0.1
        if x > 1:
            x = 0.
        mesh_box = o3d.geometry.TriangleMesh.create_box(width=1.0,
                                                        height=1.0,
                                                        depth=1.0)
        mesh_box.compute_vertex_normals()
        mesh_box.paint_uniform_color([0.9, 0.1, 0.1])
        mesh_box.translate([x, y, z])

        o3dviz.put(mesh_box)
        time.sleep(0.001)