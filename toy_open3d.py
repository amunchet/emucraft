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