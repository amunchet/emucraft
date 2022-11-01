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


def check_if_needs_z(pcd_points, dim_x, x, y):
    """
    Checks if a given point needs a Z array generation (for visualization)
    - `pcd_points` - array of points

    - Only looks behind to avoid duplicates
    """
    temp_arr = []
    x = int(x)
    y = int(y)
    dim_x = int(dim_x)
    current = pcd_points[x * dim_x + y]
    prev_x = pcd_points[(x - 1) * dim_x + y]
    prev_y = pcd_points[x * dim_x + (y-1)]

    # Check for X 


    # Check for Y
    if current[2] != prev_x[2]:
        output = add_z_points(None, (x, y, current), (x-1, y, prev_x), dim_x, None)
        temp_arr += output
    
    if current[2] != prev_y[2]:
        output = add_z_points(None, (x, y, current), (x, y-1, prev_y), dim_x, None)
        temp_arr += output

    # Return list of point groups
    return temp_arr

def parse_z_lookup(z_lookup):
    """
    Returns Z lookup as a workable array
    """
    retval = []
    for value in z_lookup.values():
        retval += value
    return retval



def stop(vis):
    """
    Callback function
    """
    pcd_points = np.asarray(pcd.points)
    z_point_cloud = o3d.geometry.PointCloud()
    vis.add_geometry(z_point_cloud)
    z_lookup = {} # Z lookup stores the (x,y) locations and is a list of the Z values

    with open("toy.xyz.sim") as f:
        current_step = 0
        for line in f.readlines():
            step, x,y,z = line.split(" ")
            

            loc = int(x) * int(dim_x) + int(y)
            pcd_points[loc][2] = z # TODO: HARD SET X SIZE TO 1000

            temp = check_if_needs_z(pcd_points, dim_x, x, y) # Only looks behind to avoid duplicates
            if temp: # Returns none if it doesn't need it
                # print("Found a temp (z-wall):", temp)
                z_lookup[(x,y)] = temp

            if step != current_step:
                vis.remove_geometry(z_point_cloud)
                z_point_cloud = o3d.geometry.PointCloud()
                intermediate_step = parse_z_lookup(z_lookup)
                z_point_cloud.points = o3d.utility.Vector3dVector(
                    np.array(intermediate_step, dtype=np.int16)
                )
                vis.add_geometry(z_point_cloud)
                # vis.update_geometry(z_point_cloud)
                vis.update_geometry(pcd)
                vis.poll_events()
                vis.update_renderer()

                current_step = step


    vis.update_geometry(pcd)
    vis.poll_events()
    vis.update_renderer()

    print("Done")
    return False

def custom_draw_geometry_load_option(pcd):
    vis = o3d.visualization.VisualizerWithKeyCallback()
    vis.register_key_callback(ord(" "), stop)

    vis.create_window()
    vis.add_geometry(pcd)

    # print("PCD points:", pcd.points)

    vis.run()
    vis.destroy_window()

if __name__ == "__main__":
    pcd = data()
    custom_draw_geometry_load_option(pcd)

