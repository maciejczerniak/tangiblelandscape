import bpy
import os
import math
import bmesh
from timeit import default_timer as timer
import json
from mathutils import Vector
from bpy.props import (
    StringProperty,
)

import bpy.utils.previews
from mathutils import Vector

bl_info = {
    "name": "Blender for Tangible Landscape",
    "author": "Payam Tabrizian (ptabriz)",
    "version": (1, 0),
    "blender": (2, 83, 0),
    "location": "Tools",
    "description": "Real-time 3D modeling with Tangible Landscape",
    "warning": "",
    "wiki_url": "https://github.com/ptabriz/tangible-landscape-immersive-extension/blob/master/README.md",
    "tracker_url": "",
    "category": "3D View",
}


watchName = "Watch"
terrainFile = "terrain.tif"
imageFile = "image.png"
waterFile = "water.tif"
viewFile = "vantage.shp"
trailFile = "trail.shp"
dynamic_cam = "dynamic_camera"
bird_cam = "bird_camera"
CRS = "EPSG:31370"


cfgFile = os.path.dirname(os.path.abspath(__file__)) + "/settings.json"

SUN_NAME = "TL_Sun"


def ensure_sun():
    obj = bpy.data.objects.get(SUN_NAME)
    if obj and obj.type == "LIGHT" and obj.data.type == "SUN":
        return obj
    for o in bpy.data.objects:
        if o.type == "LIGHT" and getattr(o.data, "type", "") == "SUN":
            return o
    light = bpy.data.lights.new(name=SUN_NAME, type="SUN")
    obj = bpy.data.objects.new(SUN_NAME, light)
    bpy.context.collection.objects.link(obj)
    light.energy = 2
    obj.location = (0, 0, 1000)
    obj.rotation_euler = (0.9, 0.9, 0)
    if hasattr(light, "shadow_cascade_max_distance"):
        light.shadow_cascade_max_distance = 1000
    return obj


def _ensure_dynamic_camera():
    cam = bpy.data.objects.get(dynamic_cam)
    tgt = bpy.data.objects.get(dynamic_cam + "_target")
    if cam is None or tgt is None:
        create_dynamic_camera()
        cam = bpy.data.objects.get(dynamic_cam)
        tgt = bpy.data.objects.get(dynamic_cam + "_target")
    return cam, tgt


def ensure_planar_uv(obj, uv_name="TL_UV", flip_v=True):
    """Create/refresh UV so UV = normalized world XY over the mesh bbox."""
    me = obj.data
    uv_layer = me.uv_layers.get(uv_name) or me.uv_layers.new(name=uv_name)
    me.uv_layers.active = uv_layer

    mw = obj.matrix_world
    xs, ys = [], []
    for v in me.vertices:
        co = mw @ v.co
        xs.append(co.x)
        ys.append(co.y)
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    dx = (xmax - xmin) or 1.0
    dy = (ymax - ymin) or 1.0

    uv_data = uv_layer.data
    for loop in me.loops:
        co = mw @ me.vertices[loop.vertex_index].co
        u = (co.x - xmin) / dx
        v = (co.y - ymin) / dy
        if flip_v:
            v = 1.0 - v
        uv_data[loop.index].uv = (u, v)
    return uv_name


def set_active_uv(obj, uv_name="TL_UV"):
    me = obj.data
    uv = me.uv_layers.get(uv_name)
    if uv:
        me.uv_layers.active = uv
        try:
            uv.active_render = True  # ensures this UV is used for render/particles
        except Exception:
            pass


def getSettings():
    with open(cfgFile, "r") as cfg:
        prefs = json.load(cfg)
    return prefs


def setSettings(prefs):
    with open(cfgFile, "w") as cfg:
        json.dump(prefs, cfg, indent="\t")


def getSetting(k):
    prefs = getSettings()
    return prefs.get(k, None)


class Prefs:
    def __init__(self):
        folder = getSettings()["folder"]
        self.watchFolder = os.path.join(folder, watchName)
        self.terrainPath = os.path.join(self.watchFolder, terrainFile)
        self.terrain_texture_path = os.path.join(self.watchFolder, imageFile)
        self.terrain_sides_texture_path = os.path.join(
            folder, getSettings()["terrain"]["sides_texture_file"]
        )
        self.world_texture_path = os.path.join(
            folder, getSettings()["world"]["texture_file"]
        )
        # self.trail_texture_path = os.path.join(
        #     folder, getSettings()["trail"]["texture_file"]
        # )
        # self.water_path = os.path.join(self.watchFolder, waterFile)
        self.view_path = os.path.join(self.watchFolder, viewFile)
        # self.trail_path = os.path.join(self.watchFolder, trailFile)
        self.CRS = "EPSG:" + getSettings()["CRS"]
        self.timer = getSettings()["timer"]
        self.scale = getSettings()["scale"]
        # self.profile = os.path.join(folder, getSettings()["trail"]["profile"])
        self.trees = {}
        for c in getSettings()["trees"]:
            self.trees[c] = {}
            self.trees[c]["model"] = os.path.join(
                folder, getSettings()["trees"][c]["model"]
            )
            self.trees[c]["texture"] = os.path.join(
                folder, getSettings()["trees"][c]["texture"]
            )


def load_objects_from_file(filepath, scale=1):
    with bpy.data.libraries.load(filepath, link=False) as (src, dst):
        dst.objects = [name for name in src.objects]
    names = []
    for obj in dst.objects:
        bpy.context.collection.objects.link(obj)
        names.append(obj.name)
        obj.scale *= scale
        obj.hide_set(True)
    return names


def assign_material(object_name, material_name):
    obj = bpy.data.objects[object_name]
    material = bpy.data.materials.get(material_name)
    # Assign it to object
    obj.data.materials.append(material)
    num_mat = len(obj.data.materials)
    if num_mat > 1:
        obj.active_material_index = num_mat - 1
        bpy.ops.object.material_slot_assign()

    print(f"Assigned {material_name} to {object_name}")


# def create_particle_system(name, particle_object_name):
def create_particle_system(
    name, particle_object_name, mask_path=None, terrain_obj=None
):
    if terrain_obj is None:
        print("Error: No terrain object provided for particle system.")
        return

    # Create particle system on terrain
    mod = terrain_obj.modifiers.new(name=name, type="PARTICLE_SYSTEM")
    psys = mod.particle_system
    settings = psys.settings
    settings.name = name
    settings.count = 1000
    settings.render_type = "OBJECT"
    settings.instance_object = bpy.data.objects[particle_object_name]
    settings.use_rotations = True
    settings.rotation_mode = "OB_Z"
    settings.particle_size = 1
    settings.size_random = 0.5
    settings.distribution = "RAND"

    # Attach texture to particle system
    if mask_path:
        image = bpy.data.images.load(mask_path)
        tex = bpy.data.textures.new(name + "_texture", type="IMAGE")
        tex.image = image
        tex.use_color_ramp = False
        tex.use_alpha = False

        mtex = settings.texture_slots.add()
        mtex.texture = tex
        mtex.use_map_density = True
        mtex.blend_type = "SUBTRACT"
        mtex.texture_coords = "UV" if terrain_obj.data.uv_layers else "ORCO"

    print("Using particle object:", particle_object_name)
    print("Instance object:", bpy.data.objects.get(particle_object_name))
    print("Terrain object:", terrain_obj.name)

    # tex = bpy.data.textures.new(name, type="IMAGE")
    # # tex.image = bpy.data.images.load(filepath=texture_path)

    # tmp_plane = "Plane"
    # bpy.ops.mesh.primitive_plane_add()
    # obj = bpy.data.objects[tmp_plane]
    # mod = obj.modifiers.new(name=name, type="PARTICLE_SYSTEM")
    # mod.particle_system.settings.name = name
    # psys = bpy.data.particles[name]
    # mtex = psys.texture_slots.add()
    # mtex.texture = tex
    # psys.distribution = "RAND"
    # psys.render_type = "OBJECT"
    # psys.use_rotations = True
    # psys.rotation_mode = "OB_Z"
    # psys.use_rotation_instance = True
    # psys.phase_factor_random = 2
    # psys.particle_size = 1
    # psys.size_random = 0.5
    # psys.count = 1000
    # psys.use_emit_random = True
    # psys.use_modifier_stack = True
    # psys.use_even_distribution = False

    # psys.instance_object = bpy.data.objects[particle_object_name]
    # psys.use_fake_user = True

    # remove_object(tmp_plane)


def create_terrain_material(name, texture_path, sides):
    # create material
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes["Principled BSDF"]
    output = nodes["Material Output"]
    tex_image = nodes.new("ShaderNodeTexImage")
    tex_image.image = bpy.data.images.load(texture_path)
    if not sides:
        tex_image.texture_mapping.scale.xyz = 1
    if sides:
        print(f"Created {name} material from {texture_path} for sides")
    coor = nodes.new("ShaderNodeTexCoord")

    mat.node_tree.links.new(
        coor.outputs["Object" if sides else "UV"], tex_image.inputs["Vector"]
    )
    # Link image to Shading node color
    mat.node_tree.links.new(bsdf.inputs["Base Color"], tex_image.outputs["Color"])
    # Link shading node to surface of output material
    mat.node_tree.links.new(output.inputs["Surface"], bsdf.outputs["BSDF"])
    bsdf.inputs["Roughness"].default_value = 0.8


def create_trail_material(name, texture_path):
    # create material
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes["Principled BSDF"]
    output = nodes["Material Output"]
    tex_image = nodes.new("ShaderNodeTexImage")
    tex_image.image = bpy.data.images.load(texture_path)
    # TODO: what about scale here?
    tex_image.texture_mapping.scale.xyz = 100
    # Link image to Shading node color
    mat.node_tree.links.new(bsdf.inputs["Base Color"], tex_image.outputs["Color"])
    # Link shading node to surface of output material
    mat.node_tree.links.new(output.inputs["Surface"], bsdf.outputs["BSDF"])
    bsdf.inputs["Roughness"].default_value = 0.8


def create_fast_water_material(name):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    output = nodes["Material Output"]
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    node_to_delete = nodes["Principled BSDF"]
    nodes.remove(node_to_delete)
    diffuse.inputs[0].default_value = (0.1, 0.2, 0.8, 1)
    mix.inputs[0].default_value = 0.6
    mat.node_tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    mat.node_tree.links.new(diffuse.outputs["BSDF"], mix.inputs[2])
    mat.node_tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])


def create_water_material(name):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    output = nodes["Material Output"]
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    noise = nodes.new("ShaderNodeTexNoise")
    glossy = nodes.new("ShaderNodeBsdfGlossy")
    node_to_delete = nodes["Principled BSDF"]
    nodes.remove(node_to_delete)
    glossy.inputs[0].default_value = (0.5, 0.6, 0.8, 1)

    glossy.inputs[1].default_value = 0.1
    mix.inputs[0].default_value = 0.6
    noise.inputs[2].default_value = 5
    noise.inputs[3].default_value = 5
    noise.inputs[4].default_value = 1
    noise.inputs[5].default_value = 0.1
    mat.node_tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    mat.node_tree.links.new(glossy.outputs["BSDF"], mix.inputs[2])
    mat.node_tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    mat.node_tree.links.new(noise.outputs["Fac"], output.inputs["Displacement"])


def create_world(name, texture_path):
    world = bpy.data.worlds.new(name=name)
    world.use_nodes = True
    nodes = world.node_tree.nodes
    coor = nodes.new("ShaderNodeTexCoord")
    tex_image = nodes.new("ShaderNodeTexImage")
    tex_image.image = bpy.data.images.load(texture_path)
    tex_image.extension = "EXTEND"
    bg = world.node_tree.nodes["Background"]
    out = world.node_tree.nodes["World Output"]
    world.node_tree.links.new(coor.outputs["Window"], tex_image.inputs["Vector"])
    world.node_tree.links.new(tex_image.outputs["Color"], bg.inputs["Color"])
    world.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return world


def add_sun():
    # keep the public function, but make it safe
    ensure_sun()


def addSide(objName, mat):
    ter = bpy.data.objects[objName]
    fringe = ter.dimensions.x / 20
    ter.select_set(True)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    me = ter.data

    if ter.mode == "EDIT":
        bm = bmesh.from_edit_mesh(ter.data)
        vertices = bm.verts

    else:
        vertices = ter.data.vertices

    verts = [ter.matrix_world @ vert.co for vert in vertices]

    dic = {"x": [], "y": [], "z": []}
    for vert in verts:
        if not math.isnan(vert[0]):
            dic["x"].append(vert[0])
            dic["y"].append(vert[1])
            dic["z"].append(vert[2])

    xmin = min(dic["x"])
    xmax = max(dic["x"])
    ymin = min(dic["y"])
    ymax = max(dic["y"])
    zmin = min(dic["z"])

    tres = 0.1

    for vert in vertices:
        if vert.co[0] < xmin + tres and vert.co[0] > xmin - tres:
            vert.select_set(True)
            vert.co[2] = zmin - fringe

        elif vert.co[1] < ymin + tres and vert.co[1] > ymin - tres:
            vert.select_set(True)
            vert.co[2] = zmin - fringe

        elif vert.co[0] < xmax + tres and vert.co[0] > xmax - tres:
            vert.select_set(True)
            vert.co[2] = zmin - fringe
        elif vert.co[1] < ymax + tres and vert.co[1] > ymax - tres:
            vert.select_set(True)
            vert.co[2] = zmin - fringe

    bmesh.update_edit_mesh(me)

    def NormalInDirection(normal, direction, limit=0.5):
        return direction.dot(normal) > limit

    def GoingUp(normal, limit=0.5):
        return NormalInDirection(normal, Vector((0, 0, 1)), limit)

    def GoingDown(normal, limit=0.5):
        return NormalInDirection(normal, Vector((0, 0, -1)), limit)

    def GoingSide(normal, limit=0.4):
        return GoingUp(normal, limit) is False and GoingDown(normal, limit) is False

    bpy.ops.object.mode_set(mode="OBJECT", toggle=False)

    # Selects faces going side

    for face in ter.data.polygons:
        face.select = GoingSide(face.normal)

    bpy.ops.object.mode_set(mode="EDIT", toggle=False)

    assign_material(objName, mat)
    bpy.ops.object.material_slot_assign()

    bpy.ops.object.mode_set(mode="OBJECT", toggle=False)


def create_dynamic_camera():
    scn = bpy.context.scene
    cam = bpy.data.cameras.new(dynamic_cam)
    cam_obj = bpy.data.objects.new(dynamic_cam, cam)
    scn.collection.objects.link(cam_obj)
    target = bpy.data.objects.new(dynamic_cam + "_target", None)
    scn.collection.objects.link(target)
    cam_obj.constraints.new("TRACK_TO")
    cam_obj.constraints["Track To"].target = target
    cam_obj.constraints["Track To"].track_axis = "TRACK_NEGATIVE_Z"
    cam_obj.constraints["Track To"].up_axis = "UP_Y"
    cam_obj.hide_set(True)
    target.hide_set(True)
    cam_obj.data.show_passepartout = False
    cam_obj.data.angle = 1.39626


def create_bird_cameras():
    scn = bpy.context.scene
    for cam in range(5):
        name = f"{bird_cam}_{cam}"
        cam = bpy.data.cameras.new(name)
        cam_obj = bpy.data.objects.new(name, cam)
        scn.collection.objects.link(cam_obj)
        cam_obj.hide_set(True)
        cam_obj.data.show_passepartout = False
        cam_obj.data.angle = 1.39626
        cam_obj.constraints.new("TRACK_TO")
        cam_obj.constraints["Track To"].track_axis = "TRACK_NEGATIVE_Z"
        cam_obj.constraints["Track To"].up_axis = "UP_Y"


def toggle_bird_cameras():
    camera_names = []
    for obj in bpy.data.objects:
        if obj.name.startswith(bird_cam):
            camera_names.append(obj.name)

    if bpy.context.scene.camera is None:
        bpy.context.scene.camera = bpy.data.objects[camera_names[0]]

    current_cam = bpy.context.scene.camera
    if current_cam.name in camera_names:
        idx = camera_names.index(current_cam.name)
    else:
        idx = 0
    idx += 1
    if idx == len(camera_names):
        idx = 0
    camera = camera_names[idx]
    toggle_camera(camera)


def toggle_camera(name):
    camera = bpy.data.objects[name]
    bpy.context.scene.camera = camera
    bpy.context.view_layer.objects.active = camera

    area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
    area.spaces[0].region_3d.view_perspective = "CAMERA"
    bpy.ops.view3d.view_center_camera()


def select_only(object_name):
    """selects the passed object"""

    if bpy.data.objects.get(object_name):
        obj = bpy.data.objects[object_name]
        if obj.hide_get():
            obj.hide_set(False)
        # Deselect all
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        return obj
    return None


def remove_object(object_name):
    if bpy.data.objects.get(object_name):
        bpy.data.objects.remove(bpy.data.objects[object_name])


def adjust_bird_cameras(object):
    dst = round(max(object.dimensions))
    k = 1.5  # increase factor
    kdst = dst * k

    def circle(r, n):
        return [
            (math.cos(2 * math.pi / n * x) * r, math.sin(2 * math.pi / n * x) * r)
            for x in range(1, n + 1)
        ]

    count = 0
    for obj in bpy.data.objects:
        if obj.name.startswith(bird_cam):
            count += 1
    positions = circle(kdst, count)
    for obj, pos in zip(bpy.data.objects, positions):
        if obj.name.startswith(bird_cam):
            x, y = pos
            obj.location.x = x
            obj.location.y = y
            obj.location.z = dst
            obj.constraints["Track To"].target = object
            obj.data.clip_end = k * kdst


def adjust_sun(obj):
    dst = round(max(obj.dimensions))
    kdst = dst * 2
    sun_obj = ensure_sun()
    sun_obj.location.z = dst
    # Eevee-only property; guard it
    if hasattr(sun_obj.data, "shadow_cascade_max_distance"):
        sun_obj.data.shadow_cascade_max_distance = kdst


def adjust3Dview(object):
    """Adjust all 3d views clip distance to match the submited bbox.
    From BlenderGIS addon."""
    dst = round(max(object.dimensions))
    k = 5  # increase factor
    dst = dst * k
    # set each 3d view
    areas = bpy.context.screen.areas
    for area in areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            if dst < 100:
                space.clip_start = 1
            elif dst < 1000:
                space.clip_start = 10
            else:
                space.clip_start = 100
            # Adjust clip end distance if the new obj is largest than actual setting
            if space.clip_end < dst:
                if dst > 10000000:
                    dst = 10000000  # too large clip distance broke the 3d view
                space.clip_end = dst
            overrideContext = bpy.context.copy()
            overrideContext["area"] = area
            overrideContext["region"] = area.regions[-1]
            bpy.ops.view3d.view_selected(overrideContext)


class Adapt:
    def __init__(self):
        self.plane = "terrain"
        self.treePatch = "TreePatch"
        # self.trail = "trail"
        # self.texture = "texture.tif"
        # self.water = "water"
        self.view = "vantage"
        # self.trail = "trail"
        self.dimensions = None

    def terrainChange(self, path, imagePath, CRS):
        # TODO: apply previous particle systems
        adjust_view = True
        if bpy.data.objects.get(self.plane):
            adjust_view = False
        remove_object(self.plane)
        bpy.ops.importgis.georaster(
            filepath=path,
            importMode="DEM",
            subdivision="mesh",
            step=2,
            rastCRS=CRS,
        )
        bpy.context.view_layer.update()

        select_only(self.plane)
        bpy.ops.object.convert(target="MESH")
        # APPLY TRANSFORMS BEFORE UV MAPPING
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        t_obj = bpy.data.objects[self.plane]
        ensure_planar_uv(
            t_obj, "TL_UV", flip_v=True
        )  # change to flip_v=False if mask looks vertically mirrored
        set_active_uv(t_obj, "TL_UV")
        # make sure TL_UV is the render UV too
        try:
            t_obj.data.uv_layers["TL_UV"].active_render = True
        except Exception:
            pass

        # t_obj = bpy.data.objects["terrain"]
        # uv_name = ensure_planar_uv(t_obj, "TL_UV", flip_v=True)
        # # bpy.ops.transform.resize(value=(1, 1, 5))
        # # bpy.ops.transform.translate(value=(0, 0, 0))
        self.dimensions = bpy.data.objects["terrain"].dimensions
        assign_material(self.plane, material_name="terrain_material")
        addSide(self.plane, "terrain_sides_material")
        try:
            os.remove(path)
        except OSError:
            pass
        if adjust_view:
            t = bpy.data.objects.get(self.plane)
            adjust3Dview(t)
            adjust_bird_cameras(t)
            adjust_sun(t)
        else:
            for obj in bpy.data.objects:
                if obj.name.startswith(bird_cam):
                    obj.constraints["Track To"].target = bpy.data.objects[self.plane]

    def waterFill(self, path, CRS):
        remove_object(self.water)
        bpy.ops.importgis.georaster(
            filepath=path, importMode="DEM", subdivision="mesh", step=2, rastCRS=CRS
        )
        select_only(self.water)
        bpy.ops.object.convert(target="MESH")
        assign_material(self.water, material_name="water_material")
        bpy.context.object.active_material.blend_method = "BLEND"
        os.remove(path)

    def camera_view(self, path, CRS):
        # re-import vantage line
        remove_object(self.view)
        bpy.ops.importgis.shapefile(filepath=path, shpCRS=CRS)
        van_line = bpy.data.objects.get(self.view)
        van_line.hide_set(True)
        if not van_line:
            print(f"camera_view: object '{self.view}' not found after import")
            return

        # make sure the dynamic camera exists
        cam, target = _ensure_dynamic_camera()

        # get evaluated mesh (Blender 2.8+/3.x way)
        deps = bpy.context.evaluated_depsgraph_get()
        eval_obj = van_line.evaluated_get(deps)

        me = eval_obj.to_mesh()
        try:
            if len(me.vertices) < 2:
                print("camera_view: vantage line has < 2 vertices; skipping")
                return

            # first vertex = camera, last vertex = look target
            cam.location = (
                me.vertices[0].co.x,
                me.vertices[0].co.y,
                me.vertices[0].co.z + 5,
            )
            target.location = (
                me.vertices[-1].co.x,
                me.vertices[-1].co.y,
                me.vertices[0].co.z + 2,
            )
        finally:
            # free the temp mesh
            eval_obj.to_mesh_clear()

        toggle_camera(dynamic_cam)

        try:
            os.remove(path)
        except OSError:
            pass

    def trees(self, patch_files, watchFolder, use_subtract=True):
        # Emitter
        try:
            terrain = bpy.data.objects[self.plane]
        except KeyError:
            print(f"[trees] no terrain for particles (plane='{self.plane}')")
            return

        # guarantee TL_UV exists & is active
        uv_name = "TL_UV"
        if (
            not getattr(terrain.data, "uv_layers", None)
            or terrain.data.uv_layers.get(uv_name) is None
        ):
            ensure_planar_uv(
                terrain, uv_name, flip_v=True
            )  # flip_v=False if you see a vertical mirror
        set_active_uv(terrain, uv_name)
        try:
            terrain.data.uv_layers[uv_name].active_render = True
        except Exception:
            pass

        # Only real patch PNGs
        files = [
            f
            for f in patch_files
            if f.lower().endswith(".png") and f.startswith("patch_")
        ]
        if not files:
            print("[trees] no patch PNGs to process")
            return

        # (optional) clear old particle systems only (keep other modifiers)
        for m in [m for m in terrain.modifiers if m.type == "PARTICLE_SYSTEM"]:
            terrain.modifiers.remove(m)

        planted = []
        for patch_file in files:
            # skip non-PNGs or sidecars just in case
            if not patch_file.lower().endswith(".png"):
                continue

            path = os.path.join(watchFolder, patch_file)
            base = os.path.splitext(patch_file)[0]
            parts = base.split("_", 1)
            if len(parts) < 2:
                print(f"[trees] skip '{patch_file}' (bad name)")
                continue

            cls = parts[1]  # e.g. 'class1'
            has_uv = (
                getattr(terrain.data, "uv_layers", None)
                and terrain.data.uv_layers.get(uv_name) is not None
            )
            if has_uv:
                set_active_uv(terrain, uv_name)

            # -----------------------------
            # Ensure Particle Settings
            # -----------------------------
            ps = bpy.data.particles.get(cls) or bpy.data.particles.new(cls)
            ps.count = (
                150  # set explicitly so reused settings don’t keep old huge counts
            )
            ps.particle_size = 0.8
            if ps.count == 0:
                ps.count = 150
            if ps.particle_size == 0.0:
                ps.particle_size = 0.8
            ps.use_modifier_stack = True
            if ps.render_type not in {"OBJECT", "COLLECTION"}:
                ps.render_type = "COLLECTION"  # safe default; we'll set a target below

            # Hair particles render immediately; keep viewport light
            ps.type = "HAIR"
            ps.use_advanced_hair = True
            ps.emit_from = "FACE"
            ps.use_emit_random = True
            ps.use_even_distribution = False
            ps.child_type = "NONE"  # no children (can explode counts)
            ps.display_percentage = 25  # lighter viewport while testing
            ps.display_step = 1
            ps.render_step = 2
            # Optional: make instances align to surface normal
            try:
                ps.use_rotations = True
                ps.rotation_mode = "GLOB_X"
            except Exception:
                pass

            # -----------------------------
            # Assign a render target (Object or Collection)
            # - If you've run "Initialize Assets", there should be an object named like the class (e.g., 'class1').
            # - Otherwise, look for a collection named 'class1' or f"{self.realism}_{class1}".
            # -----------------------------
            tree_obj = bpy.data.objects.get(cls)
            tree_coll = bpy.data.collections.get(cls) or bpy.data.collections.get(
                f"{self.realism}_{cls}"
            )

            if tree_obj:
                ps.render_type = "OBJECT"
                ps.instance_object = tree_obj
            elif tree_coll:
                ps.render_type = "COLLECTION"
                ps.instance_collection = tree_coll
                ps.use_collection_pick_random = True
            else:
                print(
                    f"[trees] Missing render target for '{cls}'. "
                    f"Create an object or collection named '{cls}' (or '{self.realism}_{cls}') "
                    f"or run Initialize Assets."
                )
                continue
                # continue, but note: without a target nothing will render

            # -----------------------------
            # Ensure Texture for Density (per class)
            # -----------------------------
            tex = bpy.data.textures.get(cls) or bpy.data.textures.new(cls, type="IMAGE")

            # keep one clean density mapping per class
            for idx in reversed(range(len(ps.texture_slots))):
                try:
                    ps.texture_slots.clear(idx)
                except Exception:
                    pass

            slot = ps.texture_slots.add()
            slot.texture = tex
            slot.texture_coords = "UV"
            slot.uv_layer = "TL_UV"
            slot.use_map_density = True
            # no RGB→intensity conversion, no alpha influence
            tex.use_color_ramp = False
            tex.use_alpha = False

            # -----------------------------
            # Load image into the texture (fresh each time; no cache)
            # -----------------------------
            # Remove any image that points to this filepath OR has the same name
            for im in list(bpy.data.images):
                try:
                    if im.name == patch_file or bpy.path.abspath(
                        im.filepath
                    ) == bpy.path.abspath(path):
                        bpy.data.images.remove(im, do_unlink=True)
                except Exception:
                    pass

            # strict data behavior
            tex.extension = "CLIP"
            tex.use_interpolation = False

            img = bpy.data.images.load(path, check_existing=False)
            img.colorspace_settings.name = "Non-Color"  # treat as a mask
            tex.image = img

            slot.blend_type = "SUBTRACT"

            # optional: hard threshold (binary mask)
            tex.use_color_ramp = True
            ramp = tex.color_ramp
            while len(ramp.elements) > 2:
                ramp.elements.remove(ramp.elements[-1])
            ramp.elements[0].position = 0.499
            ramp.elements[0].color = (0, 0, 0, 1)
            ramp.elements[1].position = 0.5
            ramp.elements[1].color = (1, 1, 1, 1)

            # Pack AFTER assigning, so the texture survives file moves/overwrites
            try:
                img.pack()
            except Exception:
                pass

            # -----------------------------
            # Add particle system on emitter & assign settings
            # -----------------------------
            mod = terrain.modifiers.new(name=f"PS_{cls}", type="PARTICLE_SYSTEM")
            mod.particle_system.name = cls
            mod.particle_system.settings = ps

            try:
                bpy.context.view_layer.update()
            except Exception:
                pass

            # -----------------------------
            # Pack & delete the file so it won't be reprocessed
            # -----------------------------
            # try:
            #     img.pack()
            # except Exception:
            #     pass
            # try:
            #     os.remove(path)
            # except OSError:
            #     pass

            try:
                base_noext = os.path.splitext(os.path.basename(path))[0]
                done_path = os.path.join(watchFolder, base_noext + ".done")
                if os.path.exists(done_path):
                    os.remove(done_path)
                os.replace(path, done_path)
            except Exception:
                pass

            planted.append(cls)

        print(
            f"[trees] planted: {', '.join(sorted(set(planted))) if planted else 'none'}"
        )


class ModalTimerOperator(bpy.types.Operator):
    """Operator which interatively runs from a timer"""

    bl_idname = "wm.modal_timer_operator"
    bl_label = "Modal Timer Operator"
    _timer = 0
    _timer_count = 0

    def modal(self, context, event):
        if event.type in {"RIGHTMOUSE", "ESC"}:
            return {"CANCELLED"}

        # this condition encomasses all the actions required for watching
        # the folder and related file/object operations .

        if event.type == "TIMER":

            if self._timer.time_duration != self._timer_count:
                self._timer_count = self._timer.time_duration
                fileList = os.listdir(self.prefs.watchFolder)
                try:
                    if terrainFile in fileList:
                        self.adapt.terrainChange(
                            self.prefs.terrainPath,
                            self.prefs.terrain_texture_path,
                            self.prefs.CRS,
                        )
                        print(self._timer_count)
                    # if waterFile in fileList:
                    #     self.adapt.waterFill(self.prefs.water_path, self.prefs.CRS)
                    if viewFile in fileList:
                        self.adapt.camera_view(self.prefs.view_path, self.prefs.CRS)

                    # if trailFile in fileList:
                    #     self.adapt.trails(self.prefs.trail_path, self.prefs.CRS)
                    patch_files = []
                    for f in fileList:
                        if f.startswith("patch_") and f.endswith(".png"):
                            patch_files.append(f)
                    if patch_files:
                        self.adapt.trees(patch_files, self.prefs.watchFolder)
                except RuntimeError:
                    pass

        return {"PASS_THROUGH"}

    def execute(self, context):
        wm = context.window_manager
        wm.modal_handler_add(self)

        # self.treePatch = "TreePatch"
        # self.emptyTree = "empty.txt"
        self.adaptMode = None
        self.prefs = Prefs()
        self.adapt = Adapt()
        self.adapt.realism = "High"
        for file in os.listdir(self.prefs.watchFolder):
            try:
                os.remove(os.path.join(self.prefs.watchFolder, file))
            except:
                print("Could not remove file")
        self._timer = wm.event_timer_add(self.prefs.timer, window=context.window)

        return {"RUNNING_MODAL"}

    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)


# Panel
class TL_PT_GUI(bpy.types.Panel):
    # Create a Panel in the Tool Shelf
    bl_category = "Tangible Landscape"
    bl_label = "Tangibe Landscape "
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    # Draw
    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="System options")
        row = box.row(align=True)
        row.operator("tl.assets", text="Initialize Assets", icon="MESH_CYLINDER")
        row = box.row(align=True)
        row.operator(
            "wm.modal_timer_operator", text="Turn on Watch Mode", icon="GHOST_ENABLED"
        )
        box = layout.box()
        box.alignment = "CENTER"
        box.label(text="Camera options", icon="CAMERA_DATA")
        row = box.row(align=True)
        row.operator("tl.birdcam", text="Preset Bird views", icon="VIEW_CAMERA")

        box = layout.box()
        box.label(text="Remove")

        row1 = box.row()
        row1.operator(
            "objects.operator", text="Remove trees", icon="RNDCURVE"
        ).button = "TREES"
        # row2 = box.row()
        # row2.operator(
        #     "objects.operator", text="Remove trail", icon="IPO_EASE_IN_OUT"
        # ).button = "TRAIL"


class TL_OT_Assets(bpy.types.Operator):
    bl_idname = "tl.assets"
    bl_label = "Asset initialization"

    def execute(self, context):
        # one-shot setup; DO NOT add a modal handler or timers here
        self.treePatch = "TreePatch"
        self.emptyTree = "empty.txt"
        self.prefs = Prefs()

        def _origin_to_bottom(obj):
            """Move mesh data so object's origin is at its lowest point (local Z)."""
            if obj.type != "MESH" or not obj.data.vertices:
                return
            # bound_box is in local space
            min_z = min(c[2] for c in obj.bound_box)
            if abs(min_z) > 1e-6:
                for v in obj.data.vertices:
                    v.co.z -= min_z
                obj.data.update()

        # ensure one instancer per class key
        for class_key, info in self.prefs.trees.items():
            if bpy.data.objects.get(class_key):
                continue
            names = load_objects_from_file(info["model"], scale=self.prefs.scale)
            mesh_obj = next(
                (
                    bpy.data.objects[n]
                    for n in names
                    if bpy.data.objects[n].type == "MESH"
                ),
                None,
            )
            if not mesh_obj:
                print(f"No mesh in {info['model']} for {class_key}")
                continue
            # optional: move origin to base so it sits on the surface
            _origin_to_bottom(mesh_obj)
            mesh_obj.name = class_key
            mesh_obj.hide_set(True)
            mesh_obj.hide_render = True
        self.adapt = Adapt()
        self.adapt.realism = "High"

        # terrain may not exist yet; guard it
        self.terrain = bpy.data.objects.get("terrain")

        # cleanup the watch folder
        for file in os.listdir(self.prefs.watchFolder):
            try:
                os.remove(os.path.join(self.prefs.watchFolder, file))
            except Exception:
                print("Could not remove file:", file)

        # cleanup old images / modifiers / textures safely
        for img in list(bpy.data.images):
            if "patch_" in img.name:
                try:
                    bpy.data.images.remove(img, do_unlink=True)
                except Exception:
                    pass

        if self.terrain:
            for m in list(self.terrain.modifiers):
                if m.type == "PARTICLE_SYSTEM" or "Particle" in m.name:
                    try:
                        self.terrain.modifiers.remove(m)
                    except Exception:
                        pass

        for tex in list(bpy.data.textures):
            if "class" in tex.name:
                try:
                    bpy.data.textures.remove(tex, do_unlink=True)
                except Exception:
                    pass

        return {"FINISHED"}  # <-- important: not modal anymore

    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)


class BirdCam(bpy.types.Operator):
    bl_idname = "tl.birdcam"
    bl_label = "Toogle Bird views"

    def execute(self, context):

        toggle_bird_cameras()

        return {"FINISHED"}


class ClearOperators(bpy.types.Operator):
    bl_idname = "objects.operator"
    bl_label = "Object Operators"
    button: bpy.props.StringProperty()

    def execute(self, context):
        if self.button == "TREES":
            terrain = bpy.data.objects.get("terrain")
            if terrain:
                while terrain.modifiers:
                    terrain.modifiers.remove(terrain.modifiers[-1])
        elif self.button == "TRAIL":
            remove_object("trail")

        return {"FINISHED"}


class MessageOperator(bpy.types.Operator):
    bl_idname = "error.message"
    bl_label = "Message"
    type = StringProperty()
    message = StringProperty()

    def execute(self, context):
        self.report({"INFO"}, self.message)
        print(self.message)
        return {"FINISHED"}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_popup(self, width=400, height=1000)

    def draw(self, context):
        self.layout.label(text=self.message)
