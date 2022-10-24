import open3d as o3d
import numpy as np
import random
import matplotlib.pyplot as plt
import time

def custom_draw_geometry_load_option(pcd):
    vis = o3d.visualization.Visualizer()
    vis.create_window()
    vis.add_geometry(pcd)
    # vis.get_render_option().load_from_json("../../test_data/renderoption.json")
    vis.run()
    vis.destroy_window()

"""
rng = np.random.default_rng()
pts = rng.random((100_000_000, 3), dtype=np.float32)
print(pts)
t = o3d.core.Tensor(pts)
pcd = o3d.t.geometry.PointCloud(t)
# pcd.point['colors'] = t
"""

pcd = o3d.geometry.PointCloud()


# pcd_arr = np.array([[x,y,int(random.random()*5000)] for x in range(0,5000) for y in range(0,5000)], dtype=np.int16) 

arr = []
with open("toy.block", "r") as f:
    for x, line in enumerate(f.readlines()):
        for y, item in enumerate(line.split(" ")):
            if item != "\n" and item != "":
                arr.append([x,y,int(item)])
                




"""
Need to add Z points for the visual side walls
"""

def add_z_points(block, start, diff, block_x, block_y):
    """
    Add Z points to steps
    """

    x_start, y_start, z_start = start
    x_diff, y_diff, z_diff = diff

    if(z_start[2] > z_diff[2]):
        max_z = z_start[2]
        min_z = z_diff[2]
    else:
        max_z = z_diff[2]
        min_z = z_start[2]
    
    for i in range(min_z, max_z):
        block.append([x_start, y_start, i])

# I know the block size
print("Starting to add Z points...")
dim_x = 1000
dim_y = 1000
for x in range(1, dim_x):
    for y in range(1, dim_y):
        current = arr[x * dim_x + y]
        prev_x = arr[(x - 1) * dim_x + y]
        prev_y = arr[x * dim_x + (y-1)]

        if current != prev_x:
            add_z_points(arr, (x, y, current), (x-1, y, prev_x), dim_x, dim_y)
        
        if current != prev_y:
            add_z_points(arr, (x, y, current), (x, y-1, prev_y), dim_x, dim_y)
print("Done")

pcd_arr = np.array(arr, dtype=np.int16)

start = time.perf_counter()

# pcd_arr[(pcd_arr[:,0] > 2500) & (pcd_arr[:,0] < 3500) & (pcd_arr[:,1] > 3000) & (pcd_arr[:,1] < 5000),2] -= 100

print("diff", time.perf_counter() - start)

print(pcd_arr)
# print(pcd_arr[2500:3500])

pcd.points = o3d.utility.Vector3dVector(pcd_arr)

# >>> o3d.visualization.draw_geometries([pcd])

custom_draw_geometry_load_option(pcd)

"""
mat = o3d.visualization.rendering.MaterialRecord()
mat.shader = 'defaultUnlit'
#renderer_pc = o3d.visualization.rendering.OffscreenRenderer(1920, 1080)
renderer_pc = o3d.visualization.Visualizer()
renderer_pc.create_window()
# renderer_pc.scene.set_background(np.array([0, 0, 0, 0]))
renderer_pc.add_geometry(pcd)
renderer_pc.run()
#renderer_pc.setup_camera(30.0, [0.5,0.5,0.5], [0.5, 3, 0.5], [0, 0, 1])
#img = renderer_pc.render_to_image()
#plt.imshow(img)
#plt.show()
renderer_pc.destroy_window()
#o3d.visualization.draw(pcd)
"""