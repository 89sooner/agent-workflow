import {useLayoutEffect,useMemo,useRef} from 'react';
import {InstancedMesh,Object3D} from 'three';
import {roomLayout} from './rooms';
import type {Point} from './travel';

type Part={kind:'box'|'plane';size:Point;color:string;emissive?:string;roughness?:number;rotation?:Point;points:Point[]};
function PartInstances({part}:{part:Part}){
  const mesh=useRef<InstancedMesh>(null);
  useLayoutEffect(()=>{
    const target=mesh.current;if(!target)return;
    const pose=new Object3D();
    pose.rotation.set(...(part.rotation||[0,0,0]));
    part.points.forEach((point,index)=>{pose.position.set(...point);pose.updateMatrix();target.setMatrixAt(index,pose.matrix)});
    target.instanceMatrix.needsUpdate=true;target.computeBoundingSphere();
  },[part]);
  return <instancedMesh ref={mesh} args={[undefined,undefined,part.points.length]}>
    {part.kind==='box'?<boxGeometry args={part.size}/>:<planeGeometry args={[part.size[0],part.size[1]]}/>}
    <meshStandardMaterial color={part.color} roughness={part.roughness??1} emissive={part.emissive||'#000000'} emissiveIntensity={part.emissive?0.15:0}/>
  </instancedMesh>;
}

export function RoomFurniture({areas}:{areas:ReturnType<typeof roomLayout>['areas']}){
  const parts=useMemo(()=>{
    const parts:Part[]=[];
    const all:Point[]=[];
    const rest:Point[]=[];
    const add=(points:Point[],offset:Point,part:Omit<Part,'points'>)=>{
      if(points.length)parts.push({...part,points:points.map(p=>[p[0]+offset[0],p[1]+offset[1],p[2]+offset[2]] as Point)});
    };
    for(const area of areas){
      // Furnish each room for its occupants, rather than duplicating the full population in every room.
      const points:Point[]=Array.from({length:Math.max(4,Math.ceil(area.count/2)*2)},(_,i)=>[
        area.x+(i%2?1.5:-1.5),0,area.center-area.depth/2+1.4+Math.floor(i/2)*2.8]);
      all.push(...points);
      if(area.id==='work'){
        add(points,[0,0.75,-0.2],{kind:'box',size:[1.9,0.12,1],color:'#34475e'});
        add(points.flatMap(p=>[-.75,.75].map(x=>[p[0]+x,p[1],p[2]] as Point)),[0,.38,-.2],{kind:'box',size:[.08,.75,.65],color:'#26364a'});
        add(points,[-.5,1.14,-.5],{kind:'box',size:[.8,.56,.1],color:'#131d2d'});
        add(points,[-.5,1.14,-.435],{kind:'plane',size:[.69,.43,0],color:'#557d91',emissive:'#557d91'});
        add(points,[0,.816,.13],{kind:'plane',size:[.8,.26,0],color:'#7890aa',rotation:[-Math.PI/2,0,0]});
      }else{
        rest.push(...points);
        add(points,[0,.43,.55],{kind:'box',size:[.85,.18,.65],color:area.id==='lounge'?'#57749a':'#96724f',roughness:.9});
      }
    }
    add(rest.flatMap(p=>[-.39,.39].map(x=>[p[0]+x,p[1],p[2]] as Point)),[0,.6,.55],{kind:'box',size:[.12,.3,.65],color:'#34465d'});
    add(all,[0,.4,.8],{kind:'box',size:[.65,.8,.12],color:'#22364b'});
    return parts;
  },[areas]);
  return <group>{parts.map((part,i)=><PartInstances key={i} part={part}/>)}</group>;
}
