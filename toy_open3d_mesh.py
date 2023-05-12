import numpy as np
import open3d as o3d
import threading
import time

# Generate a 1000x1000 array of initial z values
# z = np.random.rand(1000, 1000)
# z = np.full((10000,10000), 1000)
z = np.zeros((500,500))
with open("toy-final.block", "r") as f:
    for x, line in enumerate(f.readlines()):
        for y, item in enumerate(line.split(" ")):
            if item != "\n" and item != "":
                if x < 500 and y < 500:
                    z[x,y] = int(item)

# Create a mesh object
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

# Create a visualizer object and add the mesh to it
vis = o3d.visualization.Visualizer()
vis.create_window()
vis.add_geometry(mesh)

# Define a function to update the mesh on each iteration

# Increase the values between [100, 100] and [200, 200] by 1
for i in range(0,1000):
    z[100:201, 100:201] -= 1

    # Update the vertices of the mesh
    vertices = np.column_stack([xx.ravel(), yy.ravel(), z.ravel()])
    mesh.vertices = o3d.utility.Vector3dVector(vertices)
    mesh.compute_vertex_normals()

    # Remove the old mesh and add the updated mesh to the visualizer
    vis.update_geometry(mesh)
    vis.poll_events()
    vis.update_renderer()

    # Wait for 1 second before updating the mesh again
    



# Run the visualizer
vis.destroy_window()
