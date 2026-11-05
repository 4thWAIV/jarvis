from pathlib import Path
import struct,json,math,hashlib,io
from PIL import Image
source=Path.home()/'Downloads/human-brain.glb';blob=source.read_bytes();size,kind=struct.unpack_from('<II',blob,12);doc=json.loads(blob[20:20+size]);bin_offset=20+size+8;binary=blob[bin_offset:]
def accessor(index):
 a=doc['accessors'][index];v=doc['bufferViews'][a['bufferView']];fmt={5126:'f',5125:'I',5123:'H',5121:'B'}[a['componentType']];width={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[a['type']];unit=struct.calcsize(fmt)*width;stride=v.get('byteStride',unit);offset=v.get('byteOffset',0)+a.get('byteOffset',0)
 return [struct.unpack_from('<'+fmt*width,binary,offset+i*stride) for i in range(a['count'])]
identity=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]
def multiply(a,b):return [sum(a[k*4+r]*b[c*4+k] for k in range(4)) for c in range(4) for r in range(4)]
def transform(m,v,w):return [sum(m[c*4+r]*v[c] for c in range(3))+m[12+r]*w for r in range(3)]
positions=[];normals=[];faces=[];uvs=[];tangents=[]
def visit(i,parent):
 node=doc['nodes'][i];m=multiply(parent,node.get('matrix',identity))
 assert not any(k in node for k in ['rotation','translation','scale']), 'Unexpected transform representation'
 if 'mesh' in node:
  for primitive in doc['meshes'][node['mesh']]['primitives']:
   assert primitive.get('mode',4)==4
   offset=len(positions);positions.extend(transform(m,v,1) for v in accessor(primitive['attributes']['POSITION']));normals.extend(transform(m,v,0) for v in accessor(primitive['attributes']['NORMAL']));uvs.extend(accessor(primitive['attributes']['TEXCOORD_0']));tangents.extend((*transform(m,v[:3],0),v[3]) for v in accessor(primitive['attributes']['TANGENT']));indices=[v[0]+offset for v in accessor(primitive['indices'])];faces.extend(indices[i:i+3] for i in range(0,len(indices),3))
 for child in node.get('children',[]):visit(child,m)
for root in doc['scenes'][doc.get('scene',0)]['nodes']:visit(root,identity)
lo=[min(v[k] for v in positions) for k in range(3)];hi=[max(v[k] for v in positions) for k in range(3)];center=[(a+b)/2 for a,b in zip(lo,hi)];scale=20.4/(hi[0]-lo[0]);positions=[[(v[k]-center[k])*scale for k in range(3)] for v in positions]
for normal in normals:
 length=sum(x*x for x in normal)**.5 or 1
 for k in range(3):normal[k]/=length
# Subdivide topology once, retaining UVs and tangent bases for the authored surface detail.
midpoints={}
def middle(a,b):
 key=tuple(sorted((a,b)))
 if key not in midpoints:
  midpoints[key]=len(positions)
  for values in [positions,normals,uvs,tangents]:values.append([(x+y)/2 for x,y in zip(values[a],values[b])])
 return midpoints[key]
subdivided=[]
for a,b,c in faces:
 ab,bc,ca=middle(a,b),middle(b,c),middle(c,a);subdivided.extend([[a,ab,ca],[ab,b,bc],[ca,bc,c],[ab,bc,ca]])
faces=subdivided
imageIndex=doc['textures'][doc['materials'][0]['normalTexture']['index']]['source'];view=doc['bufferViews'][doc['images'][imageIndex]['bufferView']];normalImage=Image.open(io.BytesIO(binary[view.get('byteOffset',0):view.get('byteOffset',0)+view['byteLength']])).convert('RGB');pixels=normalImage.load();w,h=normalImage.size

def unit(v):
 length=sum(x*x for x in v)**.5 or 1
 return [x/length for x in v]
for i,(normal,tangent,uv) in enumerate(zip(normals,tangents,uvs)):
 n=unit(normal);t=unit(tangent[:3]);dot=sum(a*b for a,b in zip(n,t));t=unit([t[k]-dot*n[k] for k in range(3)])
 b=[(n[1]*t[2]-n[2]*t[1])*tangent[3],(n[2]*t[0]-n[0]*t[2])*tangent[3],(n[0]*t[1]-n[1]*t[0])*tangent[3]]
 # glTF UV origin and embedded image row order both start at the top.
 rgb=pixels[max(0,min(w-1,round(uv[0]*(w-1)))),max(0,min(h-1,round(uv[1]*(h-1))))];mapped=unit([rgb[k]/127.5-1 for k in range(3)])
 normals[i]=unit([t[k]*mapped[0]+b[k]*mapped[1]+n[k]*mapped[2] for k in range(3)])
# The inferior stem termination is the anatomical attachment used by this visualization.
minimum=min(v[1] for v in positions);rim=[v for v in positions if v[1]<minimum+.18];anchor=[sum(v[k] for v in rim)/len(rim) for k in range(3)]
edges=sorted({tuple(sorted((a,b))) for f in faces for a,b in zip(f,f[1:]+f[:1])})
out={'positions':[round(x,5) for v in positions for x in v],'normals':[round(x,5) for v in normals for x in v],'edges':[i for e in edges for i in e],'anchor':anchor};folder=Path(__file__).parent;(folder/'anatomy.json').write_text(json.dumps(out,separators=(',',':')))
(folder/'source-provenance.json').write_text(json.dumps({'source':doc['asset']['extras'],'sha256':hashlib.sha256(blob).hexdigest(),'vertices':len(positions),'triangles':len(faces),'edges':len(edges),'anchor':anchor,'modifications':'Applied GLB scene transforms; subdivided once and baked the authored tangent normal map into per-vertex shading normals; original mesh positions retained, except interpolated subdivision vertices. No texture is rendered. Anchor is the averaged inferior stem termination, a visualization landmark rather than a medically validated registration.'},indent=2))
print('Converted',len(positions),'vertices,',len(faces),'triangles,',len(edges),'filament edges. Anchor:',anchor)
