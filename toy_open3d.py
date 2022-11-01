import open3d as o3d
import numpy as np
import random
import matplotlib.pyplot as plt
import time



dim_x = 1000
dim_y = 1000
# Need to add Z points for the visual side walls
def add_z_points(block, start, diff, block_x, block_y):
    """
    Add Z points to steps
    """
    x_start, y_start, z_start = start
    x_diff, y_diff, z_diff = diff

    if(z_start[2] > z_diff[2]):
        max_z = int(z_start[2])
        min_z = int(z_diff[2])
    else:
        max_z = int(z_diff[2])
        min_z = int(z_start[2])
    arr = []
    for i in range(min_z, max_z):
        # block = np.append(block, [x_start, y_start, i])
        arr.append([x_start, y_start, i])
    
    return arr

def data():
    global dim_x, dim_y
    pcd = o3d.geometry.PointCloud()
    arr = []
    with open("toy.block", "r") as f:
        for x, line in enumerate(f.readlines()):
            for y, item in enumerate(line.split(" ")):
                if item != "\n" and item != "":
                    arr.append([x,y,int(item)])
                    


    
    
    print("Done")

    pcd_arr = np.array(arr, dtype=np.int16)
    print(pcd_arr)

    pcd.points = o3d.utility.Vector3dVector(pcd_arr)
    return pcd


special_run = False

def custom_draw_geometry_load_option(pcd):
    global arr
    vis = o3d.visualization.VisualizerWithKeyCallback()

    def stop(vis):
        global arr
        pcd_points = np.asarray(pcd.points)

        global special_run
        special_run = not special_run

        with open("toy.xyz.sim") as f:
            current_step = 0
            for line in f.readlines():
                step, x,y,z = line.split(" ")
                

                loc = int(x) * int(dim_x) + int(y)
                pcd_points[loc][2] = z # TODO: HARD SET X SIZE TO 1000


                if step != current_step:
                    vis.update_geometry(pcd)
                    vis.poll_events()
                    vis.update_renderer()

                    current_step = step

        # I know the block size
        # TODO - This is currently broken
        print("Starting to add Z points...")
        temp_arr = []
        arr = pcd_points

        for x in range(1, dim_x):
            for y in range(1, dim_y):
                current = arr[x * dim_x + y]
                prev_x = arr[(x - 1) * dim_x + y]
                prev_y = arr[x * dim_x + (y-1)]


                if current[2] != prev_x[2]:
                    output = add_z_points(arr, (x, y, current), (x-1, y, prev_x), dim_x, dim_y)
                    temp_arr += output
                
                if current[2] != prev_y[2]:
                    output = add_z_points(arr, (x, y, current), (x, y-1, prev_y), dim_x, dim_y)
                    temp_arr += output



        print("Temp Arr:", temp_arr[:5])

        temp_pcd = o3d.geometry.PointCloud()
        temp_pcd_arr = np.array(temp_arr, dtype=np.int16)
        print(temp_pcd_arr)

        temp_pcd.points = o3d.utility.Vector3dVector(temp_pcd_arr)
        vis.add_geometry(temp_pcd)



        vis.update_geometry(pcd)
        vis.poll_events()
        vis.update_renderer()

        return False

    vis.register_key_callback(ord(" "), stop)

    vis.create_window()
    vis.add_geometry(pcd)

    print("PCD points:", pcd.points)
    vis.run()

    vis.destroy_window()

if __name__ == "__main__":
    pcd = data()
    custom_draw_geometry_load_option(pcd)

