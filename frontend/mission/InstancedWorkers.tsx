import {useEffect,useRef} from 'react';
import {useFrame,type ThreeEvent} from '@react-three/fiber';
import {DynamicDrawUsage,Group,InstancedMesh,Matrix4,Mesh,Material} from 'three';
import type {Node} from './types';

type Entry={source:Mesh;actor:string;raycast:Mesh['raycast'];previous:Matrix4};
type Batch={mesh:InstancedMesh;entries:Entry[]};
const shapes=new Set(['BoxGeometry','SphereGeometry','CapsuleGeometry','CylinderGeometry','RingGeometry','OctahedronGeometry']);

export function InstancedWorkers({actors,onSelect}:{actors:Map<string,Group>;onSelect:(node:Node)=>void}){
  const root=useRef<Group>(null),batches=useRef<Batch[]>([]),revision=useRef('');
  const matrix=useRef(new Matrix4()),inverse=useRef(new Matrix4());
  const clear=()=>{
    for(const batch of batches.current){
      root.current?.remove(batch.mesh);
      batch.entries.forEach(({source,raycast})=>{source.visible=true;source.raycast=raycast});
      batch.mesh.dispose();batch.mesh.geometry.dispose();
      (batch.mesh.material as Material).dispose();
    }
    batches.current=[];
  };
  useEffect(()=>()=>clear(),[]);
  useFrame(()=>{
    const target=root.current;if(!target)return;
    const next=[...actors].map(([id,group])=>id+':'+group.userData.batchRevision).join('|');
    if(next!==revision.current){
      clear();revision.current=next;
      const groups=new Map<string,Entry[]>();
      for(const [actor,group] of actors){
        group.traverse(object=>{
          if(!(object instanceof Mesh)||!object.visible||!shapes.has(object.geometry.type)||Array.isArray(object.material))return;
          const {uuid:_id,metadata:_metadata,name:_name,...properties}=object.material.toJSON();
          const geometry=object.geometry as typeof object.geometry&{parameters:unknown};
          const key=geometry.type+JSON.stringify(geometry.parameters)+JSON.stringify(properties);
          const entries=groups.get(key)||[];
          const previous=new Matrix4();previous.elements.fill(Infinity);
          entries.push({source:object,actor,raycast:object.raycast,previous});groups.set(key,entries);
        });
      }
      for(const entries of groups.values()){
        const first=entries[0].source;
        const mesh=new InstancedMesh(first.geometry.clone(),(first.material as Material).clone(),entries.length);
        mesh.instanceMatrix.setUsage(DynamicDrawUsage);
        mesh.userData.actorIds=entries.map(entry=>entry.actor);
        entries.forEach(({source})=>{source.visible=false;source.raycast=()=>{}});
        target.add(mesh);batches.current.push({mesh,entries});
      }
    }
    // Pose updates run at -2. Publish their matrices before camera following and rendering.
    for(const group of actors.values())group.updateWorldMatrix(true,true);
    target.updateWorldMatrix(true,false);inverse.current.copy(target.matrixWorld).invert();
    for(const {mesh,entries} of batches.current){
      let changed=false;
      entries.forEach(({source,previous},i)=>{
        matrix.current.multiplyMatrices(inverse.current,source.matrixWorld);
        if(!previous.equals(matrix.current)){
          previous.copy(matrix.current);mesh.setMatrixAt(i,matrix.current);changed=true;
        }
      });
      if(changed){mesh.instanceMatrix.needsUpdate=true;mesh.computeBoundingSphere()}
    }
  },-1.5);
  const select=(event:ThreeEvent<MouseEvent>)=>{
    const id=event.instanceId===undefined?undefined:event.object.userData.actorIds?.[event.instanceId];
    const node=id?actors.get(id)?.userData.node:undefined;
    if(node){event.stopPropagation();onSelect(node)}
  };
  return <group ref={root} onClick={select}/>;
}
