import open3d as o3d

# Create a sphere geometry
sphere = o3d.geometry.TriangleMesh.create_sphere(radius=1.0)
sphere.compute_vertex_normals()
# Save the sphere to an STL file
o3d.io.write_triangle_mesh("sphere.stl", sphere)