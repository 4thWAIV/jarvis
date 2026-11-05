"""Trace continuous, curvature-guided paths over the actual anatomical mesh.
No image sampling, axis stripes or clipping planes are used to select paths.
"""
import json,math
from pathlib import Path
root=Path(__file__).parent
a=json.loads((root/'anatomy.json').read_text())
p=[a['positions'][i:i+3] for i in range(0,len(a['positions']),3)]
n=[a['normals'][i:i+3] for i in range(0,len(a['normals']),3)]
N=len(p);adj=[set() for _ in p]
for i in range(0,len(a['edges']),2):
 x,y=a['edges'][i:i+2];adj[x].add(y);adj[y].add(x)
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def sub(a,b):return [x-y for x,y in zip(a,b)]
def unit(v):
 d=math.sqrt(dot(v,v)) or 1;return [x/d for x in v]
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
# Smooth the authored normal field locally to avoid chasing single-texel noise.
for _ in range(2):n=[unit([v[k]*2+sum(n[j][k] for j in adj[i]) for k in range(3)]) for i,v in enumerate(n)]
def solve(M,v):
 m=[M[i][:]+[v[i]] for i in range(3)]
 for i in range(3):
  j=max(range(i,3),key=lambda j:abs(m[j][i]));m[i],m[j]=m[j],m[i]
  if abs(m[i][i])<1e-12:return [0,0,0]
  d=m[i][i];m[i]=[x/d for x in m[i]]
  for j in range(3):
   if i!=j:
    d=m[j][i];m[j]=[x-d*y for x,y in zip(m[j],m[i])]
 return [m[i][3] for i in range(3)]
fields=[];curvature=[]
for i,normal in enumerate(n):
 U=unit(cross(normal,[0,1,0] if abs(normal[1])<.9 else [1,0,0]));V=cross(normal,U)
 M=[[0.,0.,0.] for _ in range(3)];rhs=[0.,0.,0.]
 for j in adj[i]:
  e=sub(p[j],p[i]);dn=sub(n[j],normal);x,y=dot(e,U),dot(e,V)
  for row,value in [([x,y,0],dot(dn,U)),([0,x,y],dot(dn,V))]:
   for r in range(3):
    rhs[r]+=row[r]*value
    for c in range(3):M[r][c]+=row[r]*row[c]
 aa,b,cc=solve(M,rhs);disc=math.sqrt((aa-cc)**2+4*b*b);eigen=[(aa+cc-disc)/2,(aa+cc+disc)/2];lam=min(eigen,key=abs)
 uv=unit([b,lam-aa,0]) if abs(b)>1e-8 else ([1,0,0] if abs(aa)<abs(cc) else [0,1,0])
 fields.append([U[k]*uv[0]+V[k]*uv[1] for k in range(3)]);curvature.append(max(abs(eigen[0]),abs(eigen[1])))
used=set();paths=[]
def walk(seed,direction,local):
 result=[];current=seed;travel=0
 for _ in range(90):
  tangent=fields[current];tangent=[x*(1 if dot(tangent,direction)>=0 else -1) for x in tangent]
  desired=unit([direction[k]*.7+tangent[k]*.3 for k in range(3)])
  candidates=[]
  for j in adj[current]:
   if j in local or j in used:continue
   delta=sub(p[j],p[current]);length=math.sqrt(dot(delta,delta));d=unit(delta);alignment=dot(d,desired)
   if alignment<.15 or length<1e-6:continue
   score=alignment*.8+abs(dot(d,fields[j]))*.2
   candidates.append((score,j,d,length))
  if not candidates:break
  _,j,d,length=max(candidates);travel+=length
  if travel>16:break
  result.append(j);local.add(j);current=j;direction=d
 return result
# Start at the strongest folds; preserve broad surface coverage with a local exclusion.
seeds=sorted(range(N),key=lambda i:curvature[i],reverse=True)
for seed in seeds:
 if seed in used:continue
 local={seed};forward=walk(seed,fields[seed],local);back=walk(seed,[-x for x in fields[seed]],local)
 path=list(reversed(back))+[seed]+forward
 if len(path)<10:continue
 paths.append(path)
 for i in path:
  used.add(i);used.update(adj[i])
 if len(paths)>=900:break
# Interleave spatial octants so every lighting level has distributed anatomical paths.
center=[(min(v[k] for v in p)+max(v[k] for v in p))/2 for k in range(3)]
bins=[[] for _ in range(8)]
for path in paths:
 mid=p[path[len(path)//2]];octant=sum((mid[k]>center[k])<<k for k in range(3));bins[octant].append(path)
ordered=[]
while any(bins):
 for bucket in bins:
  if bucket:ordered.append(bucket.pop(0))
result={'center':center,'sourceVertices':N,'paths':[]}
for ids in ordered:
 result['paths'].append({'positions':[round(x,5) for i in ids for x in p[i]],'normals':[round(x,5) for i in ids for x in n[i]]})
(root/'filaments.json').write_text(json.dumps(result,separators=(',',':')))
print(f'Traced {len(ordered)} anatomical paths; {sum(len(x) for x in ordered)} connected surface vertices.')
