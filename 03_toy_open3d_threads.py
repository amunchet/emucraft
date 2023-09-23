import time
import threading
import numpy as np
import open3d as o3d
import open3d.visualization.gui as gui
import open3d.visualization.rendering as rendering

def load_block():
    dim_x = 500
    dim_y = 500

    z = np.zeros((dim_x,dim_y))

    """
    with open("toy-final.block", "r") as f:
        for x, line in enumerate(f.readlines()):
            for y, item in enumerate(line.split(" ")):
                if item != "\n" and item != "":
                    z[x,y] = int(item) / 10
                    z[x,y] = 200
    """
    for x in range(0,dim_x):
        for y in range(0,dim_y):
            z[x,y] = 200
    print(z)
    return z

def downsample(z, dim_x=500, dim_y=500, divisor=500):
    """
    Downsampler
    """
    reshaped = z.reshape(divisor, int(dim_x/divisor), divisor, int(dim_y/divisor))
    return np.min(reshaped, axis=(1,3))

def reduce_z_values(array, center, diameter, z_value):
    """
    Sample animation function
    """
    x_center, y_center = center
    radius = diameter / 2

    x_indices, y_indices = np.indices(array.shape)
    distances = np.sqrt((x_indices - x_center)**2 + (y_indices - y_center)**2)

    mask = distances <= radius
    array[mask] = np.minimum(array[mask], z_value)



paused = False
speed = 10

def main():


    z = load_block()
    z = downsample(z)

    gui.Application.instance.initialize()

    window = gui.Application.instance.create_window('img')
    widget = gui.SceneWidget()
    widget.scene = rendering.Open3DScene(window.renderer)
    window.add_child(widget)    
    def keypress_callback(key_event):
        # Handle keypress event
        global paused
        global speed

        key = key_event.key
        if key == ord('q') or key == ord('Q'):
            gui.Application.instance.quit()
        elif key == ord('p') or key == ord('P'):
            paused = True
        elif key == ord(' '):
            paused = False
        elif key == ord("f") or key == ord("F"):
            speed = speed * 2
        
        elif key == ord("d") or key == ord("D"):
            speed = speed / 2 if speed > 2 else 1
        return True

    widget.set_on_key(keypress_callback)


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

    widget.scene.camera.look_at([0,0,0], [1000,1000,1000], [0,0,1])
    widget.scene.add_geometry('frame', frame, mat)
    widget.scene.add_geometry('mesh', mesh, mat)


    def update_geometry():
        widget.scene.clear_geometry()
        widget.scene.add_geometry('frame', frame, mat)
        widget.scene.add_geometry('mesh', mesh, mat)   

    def thread_main():
        # i = np.tile(np.arange(len(mesh.vertices)),(3,1)).T # (8,3)
        # while True:
        i = 0
        global paused
        global speed


        with open("gcode/tests/test.xyz") as f:
            lines = f.readlines()

        seen_x = None
        seen_y = None
        for i,item in enumerate(lines):
            cur_x,cur_y,cur_z,diameter,tool_diam,tool_length,move_type = [int(int(x)/10) for x in item.split(" ")]
            if not paused:
                print((cur_x, cur_y, cur_z))
                if cur_x == seen_x and cur_y == seen_y:
                    continue
                seen_x = cur_x
                seen_y = cur_y
                # print(diameter)
                reduce_z_values(z, (cur_x, cur_y), diameter, cur_z)
                if (i % speed == 0):
                    vertices = np.column_stack([xx.ravel(), yy.ravel(), z.ravel()])
                    mesh.vertices = o3d.utility.Vector3dVector(vertices)
                    mesh.compute_vertex_normals()


                    # Update geometry
                    gui.Application.instance.post_to_main_thread(window, update_geometry)            

                    time.sleep(0.05)


    threading.Thread(target=thread_main).start()

    gui.Application.instance.run()

if __name__ == "__main__":
    main()