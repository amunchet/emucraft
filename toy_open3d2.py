import open3d as o3d
import numpy as np
from time import sleep

frames = 150

vis = o3d.visualization.Visualizer()
vis.create_window()

# pcd = o3d.io.read_point_cloud(f'scratch/toy.xyz-00000.block')

arr = []
pcd = o3d.geometry.PointCloud()

with open(f'scratch/toy.xyz-00000.block') as f:
    for x, line in enumerate(f.readlines()):
        for y, item in enumerate(line.split(" ")):
            if item != "\n" and item != "":
                arr.append([x,y,int(item)])
pcd.points = o3d.utility.Vector3dVector(np.array(arr))

vis.add_geometry(pcd)
vis.poll_events()
vis.update_renderer()

for i in range(1, frames):
    #pcd.points = o3d.io.read_point_cloud(f'scratch/{i:05d}.block').points
    arr = []
    with open(f'scratch/toy.xyz-{i:05d}.block') as f:
        for x, line in enumerate(f.readlines()):
            for y, item in enumerate(line.split(" ")):
                if item != "\n" and item != "":
                    arr.append([x,y,int(item)])
    
    pcd.points = o3d.utility.Vector3dVector(np.array(arr))
    vis.update_geometry(pcd)
    vis.poll_events()
    vis.update_renderer()

    