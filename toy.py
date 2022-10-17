import trimesh
import math

scene = trimesh.scene.scene.Scene()

a = trimesh.creation.box((1,2,3))
b = trimesh.creation.box((3,4,5))

move_matrix = trimesh.transformations.translation_matrix([3,0,0])
b.apply_transform(move_matrix)

scene.add_geometry(a)
scene.add_geometry(b)

scene.export("toy.stl")