import os
import time
import threading
import queue

import open3d as o3d

o3d.visualization.webrtc_server.enable_webrtc()
from open3d.visualization import O3DVisualizer, gui

app = gui.Application.instance

app.initialize()
w = O3DVisualizer("o3dviz", 1024, 768)
w.set_background((0.0, 0.0, 50.0, 1.0), None)

app.add_window(w)


x, y, z = 0.1, 0.0, 0.0
first = True

def external_thread():
    global x,y,z
    global w
    global first
    x = x + 0.1
    if x > 1:
        x = 0.

    mesh_box = o3d.geometry.TriangleMesh.create_box(width=1.0, height=1.0, depth=1.0)
    mesh_box.compute_vertex_normals()
    mesh_box.paint_uniform_color([0.9, 0.5, 0.1])
    mesh_box.translate([x, y, z])

    # w.reset_camera_to_default()
    if not first:
        w.remove_geometry("box")
    if first:
        w.add_geometry("box", mesh_box)
        first = False
    
    # w.post_redraw()
    
    time.sleep(0.001)



while True:
    # app.post_to_main_thread(w, external_thread)
    # Add box
    x = x + 0.1
    if x > 1:
        x = 0.

    mesh_box = o3d.geometry.TriangleMesh.create_box(width=1.0, height=1.0, depth=1.0)
    mesh_box.compute_vertex_normals()
    mesh_box.paint_uniform_color([0.9, 0.5, 0.1])
    mesh_box.translate([x, y, z])

    # w.reset_camera_to_default()
    if not first:
        w.remove_geometry("box")
    if first:
        w.reset_camera_to_default()
        first = False
    else:
        w.add_geometry("box", mesh_box)
    w.post_redraw()


    app.run_one_tick()
    # time.sleep(1)
    # app.run()
