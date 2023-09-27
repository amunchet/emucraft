import os
import time
import queue

import open3d as o3d
import numpy as np
from visualize import load_block, downsample, reduce_z_values

o3d.visualization.webrtc_server.enable_webrtc()

from open3d.visualization import O3DVisualizer, gui
import open3d.visualization.rendering as rendering


app = gui.Application.instance

app.initialize()
w = O3DVisualizer("o3dviz", 1024, 768)
w.set_background((0.0, 0.0, 50.0, 1.0), None)
app.add_window(w)
w.reset_camera_to_default()

# Init

z = load_block()
z = downsample(z)


frame = o3d.geometry.TriangleMesh.create_coordinate_frame(1.5)

# mesh = o3d.geometry.TriangleMesh.create_sphere()

mesh = o3d.geometry.TriangleMesh()
# Define the vertices of the mesh
x = np.arange(z.shape[0])
y = np.arange(z.shape[1])
xx, yy = np.meshgrid(x, y)
vertices = np.column_stack([xx.ravel(), yy.ravel(), z.ravel()])

# Define the triangles of the mesh
n = z.shape[0]
triangles = []
for i in range(n-1):
    for j in range(n-1):
        v1 = i*n + j
        v2 = i*n + j + 1
        v3 = (i+1)*n + j + 1
        v4 = (i+1)*n + j
        triangles.append([v1, v2, v3])
        triangles.append([v1, v3, v4])

# Set the vertices and triangles of the mesh
mesh.vertices = o3d.utility.Vector3dVector(vertices)
mesh.triangles = o3d.utility.Vector3iVector(triangles)

mesh.compute_vertex_normals() # TODO: Maybe I can delete this?

# mat = rendering.Material()
mat = rendering.MaterialRecord()
mat.shader = 'defaultLit'

w.add_geometry("frame", frame) # TODO: add mat
w.add_geometry("mesh", mesh)

paused = False
speed = 10


w.reset_camera_to_default()

# Main Loop
i = 0

with open("gcode/tests/test.xyz") as f:
    lines = f.readlines()

seen_x = None
seen_y = None
seen_z = None
skipping_z = False
for i,item in enumerate(lines):
    cur_x,cur_y,cur_z,diameter,tool_diam,tool_length,move_type = [round(int(x)/10) for x in item.split(" ")]
    while paused:
        time.sleep(1)
    if not paused:
        # print((cur_x, cur_y, cur_z))
        if cur_x == seen_x and cur_y == seen_y:
            skipping_z = True
            continue
        
        if skipping_z:
            reduce_z_values(z, (seen_x, seen_y), diameter, seen_z)
            skipping_z = False

        seen_x = cur_x
        seen_y = cur_y
        seen_z = cur_z
        
        # print(diameter)
        reduce_z_values(z, (cur_x, cur_y), diameter, cur_z)
        if (i % speed == 0):
            vertices = np.column_stack([xx.ravel(), yy.ravel(), z.ravel()])
            mesh.vertices = o3d.utility.Vector3dVector(vertices)
            mesh.compute_vertex_normals()
    
    w.remove_geometry("frame")
    w.remove_geometry("mesh")

    w.add_geometry("frame", frame)
    w.add_geometry("mesh", mesh)



    w.post_redraw()
    app.run_one_tick()
    w.reset_camera_to_default()
    
    time.sleep(0.001)

# Continue to run after the loop is finished
app.run()