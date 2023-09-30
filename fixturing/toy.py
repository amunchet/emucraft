"""
ChatGPT helped with some of the code, but the rest came from this link:
- http://www.open3d.org/docs/latest/tutorial/geometry/ray_casting.html
"""
import sys
import open3d as o3d
import numpy as np
import time

start_time = time.time()

def ray_cast(mesh, rays):
    # Create a ray from the start_point in the specified direction

    """
    ray = o3d.geometry.TriangleMesh.create_line(origin=start_point, direction=direction)

    # Perform ray-mesh intersection test
    result = o3d.geometry.TriangleMesh.ray_test(mesh, ray)
    """
    # x,y,z = start_point
    scene = o3d.t.geometry.RaycastingScene()
    cube_id = scene.add_triangles(mesh)
    ans = scene.cast_rays(rays)
    return ans

# Load STL file and convert to mesh
mesh = o3d.io.read_triangle_mesh("kurt.stl")


# mesh = o3d.io.read_triangle_mesh("sphere.stl")




a = o3d.t.geometry.TriangleMesh()
mesh = a.from_legacy(mesh)

# Calculate the dimensions of the 2D array

a = mesh.get_axis_aligned_bounding_box()
min_bound = a.min_bound
max_bound = a.max_bound

div_factor = 39.37 # 1000 / 25.4 - I think this gives me .001" increments
# div_factor = 1000

length_x_thousandths = int((max_bound[0].item() - min_bound[0].item()) * div_factor)
length_y_thousandths = int((max_bound[1].item() - min_bound[1].item()) * div_factor)

# Create a 2D array initialized to zero
array_2d = np.zeros((length_x_thousandths, length_y_thousandths))


# Iterate through each point in the array


ray_arr = []

min_bound_x = min_bound[0].item()
min_bound_y = min_bound[1].item()
max_z = max_bound[2].item()
min_z = min_bound[2].item()

height_z = max_z - min_z


# Create arrays for x and y coordinates separately
x_coords = np.arange(0, length_x_thousandths) / div_factor + min_bound_x
y_coords = np.arange(0, length_y_thousandths) / div_factor + min_bound_y


print("X coords:", x_coords)
print("Y coords:", y_coords)

# Calculate the lengths of x_coords and y_coords
num_x_coords = len(x_coords)
num_y_coords = len(y_coords)

# Create arrays for start points (x, y, z)
start_x = np.repeat(x_coords, num_y_coords)
start_y = np.tile(y_coords, num_x_coords)
start_z = np.full_like(start_x, max_z + 1.0)

print("Start x,y,z:", start_x, start_y, start_z)

# Create an array for directions (0, 0, -1)
directions = np.column_stack((np.zeros_like(start_x), np.zeros_like(start_y), np.full_like(start_x, -1.0)))

# Combine start points and directions into ray_arr
ray_arr = np.column_stack((start_x, start_y, start_z, directions))

print("Ray arr:", ray_arr)



print("Out of main loop")
rays = o3d.core.Tensor(
    ray_arr, dtype=o3d.core.Dtype.Float32
)   
z_value = ray_cast(mesh, rays)

# z_value = ray_cast(mesh, start_point)['t_hit'].item()

# Write out to a file
print("Converting to array...")
# numpy_array = np.asarray(z_value["t_hit"])
numpy_array = (z_value["t_hit"] - height_z) * -1000

numpy_array = numpy_array / 10 # Reduce height for vis

# Specify the output file path
output_file_path = "toy.block"

print("Writing file...")
# Open the file for writing
with open(output_file_path, "w") as file:
    # Iterate through the NumPy array and write each item
    for i, item in enumerate(numpy_array):
        x = item.item()
        if x < 0:
            x = 0
        try:
            file.write(str(round(x)))
        except OverflowError:
            file.write("0")
        
        # Check if it's the 1000th item and add "\r\n"
        if (i + 1) % length_y_thousandths == 0:
            file.write("\r\n")
        else:
            file.write(" ")

# Now, 'array_2d' contains the desired 2D representation of the solid with Z values or 0 for empty space

end_time = time.time()
print("Rough time:", end_time - start_time)