import { useEffect, useRef } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { clone as cloneSkeleton } from "three/examples/jsm/utils/SkeletonUtils.js";
import { createCustomerReconstructor, layoutPositions } from "../lib/reconstructCustomers";

const MODEL_URL = "/models/Xbot.glb";
const IDLE_CLIP_NAME = "idle";
const WALK_CLIP_NAME = "walk";
const LERP_FACTOR = 0.12;
const WALK_EPSILON = 0.05;
const DEFAULT_POOL_SIZE = 12;
const POOL_HEADROOM = 4;
// Off to the side, representing the shop's entrance -- new customers appear
// here and walk in to join the line, rather than materializing wherever a
// reused pool slot's mesh last happened to be (which could be mid-scene,
// looking like they walked backward out of the counter).
const ENTRANCE = new THREE.Vector3(4.5, 0, 3.5);

function cssColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(value || fallback);
}

// Fetched/parsed exactly once for the page's lifetime and shared across every
// mount/remount of the scene (knob changes recreate the three.js scene far
// more often than the underlying model asset actually needs reloading).
let templateLoadPromise = null;
function loadTemplate() {
  if (!templateLoadPromise) {
    templateLoadPromise = new Promise((resolve, reject) => {
      new GLTFLoader().load(MODEL_URL, resolve, undefined, reject);
    });
  }
  return templateLoadPromise;
}

export default function BrewLineScene({ trace, currentTime, numBaristas, resetKey, maxConcurrency }) {
  const containerRef = useRef(null);
  const currentTimeRef = useRef(0);

  useEffect(() => {
    currentTimeRef.current = currentTime ?? 0;
  }, [currentTime]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    let disposed = false;
    const timer = new THREE.Timer();

    // Bright studio backdrop for the 3D canvas itself -- deliberately
    // lighter than the dark dashboard chrome around it, so the animation
    // reads as a lit product shot rather than blending into the dark UI.
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf5f5f4);

    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
    camera.position.set(8, 7, 10);
    camera.lookAt(0, 0.5, 3);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    const hemi = new THREE.HemisphereLight(0xffffff, 0xcccccc, 1.3);
    scene.add(hemi);
    const dir = new THREE.DirectionalLight(0xffffff, 1.6);
    dir.position.set(5, 8, 4);
    dir.castShadow = true;
    scene.add(dir);

    const idleColor = cssColor("--series-queue", "#3987e5");
    const busyColor = cssColor("--series-wip", "#d95926");

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(16, 18),
      new THREE.MeshStandardMaterial({ color: 0xe4e4e1 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(0, 0, 4); // covers the counter (z~0) and the queue line receding to z~13
    ground.receiveShadow = true;
    scene.add(ground);

    const counter = new THREE.Mesh(
      new THREE.BoxGeometry(numBaristas * 1.6 + 0.6, 0.5, 0.6),
      new THREE.MeshStandardMaterial({ color: 0x898781 }),
    );
    counter.position.set(0, 0.25, -0.4);
    counter.castShadow = true;
    counter.receiveShadow = true;
    scene.add(counter);

    const stationMarkers = [];
    for (let i = 0; i < numBaristas; i++) {
      const marker = new THREE.Mesh(
        new THREE.CylinderGeometry(0.28, 0.28, 0.06, 24),
        new THREE.MeshStandardMaterial({ color: idleColor }),
      );
      marker.position.set((i - (numBaristas - 1) / 2) * 1.6, 0.03, 0);
      marker.receiveShadow = true;
      scene.add(marker);
      stationMarkers.push(marker);
    }

    // Object pool -- assigned/released by cid rather than created/destroyed
    // per frame, since a rigged SkinnedMesh clone is comparatively heavy.
    const poolSize = Math.max(DEFAULT_POOL_SIZE, (maxConcurrency ?? 0) + POOL_HEADROOM);
    const pool = []; // { root, mixer, idleAction, walkAction, cid: number|null }
    const activeByCid = new Map(); // cid -> pool entry

    let template = null;
    let templateClips = null;

    loadTemplate().then(
      (gltf) => {
        if (disposed) return;
        template = gltf.scene;
        templateClips = gltf.animations;
        for (let i = 0; i < poolSize; i++) {
          pool.push(spawnInstance());
        }
      },
      (err) => {
        if (disposed) return;
        console.error("BrewLineScene: failed to load humanoid model", err);
      },
    );

    function spawnInstance() {
      const root = cloneSkeleton(template);
      root.scale.setScalar(0.9);
      root.traverse((obj) => {
        if (obj.isMesh) {
          obj.castShadow = true;
          obj.receiveShadow = true;
        }
      });
      scene.add(root);
      root.visible = false;

      const mixer = new THREE.AnimationMixer(root);
      const idleClip = THREE.AnimationClip.findByName(templateClips, IDLE_CLIP_NAME);
      const walkClip = THREE.AnimationClip.findByName(templateClips, WALK_CLIP_NAME);
      const idleAction = mixer.clipAction(idleClip);
      const walkAction = mixer.clipAction(walkClip);
      idleAction.play();
      walkAction.play();
      walkAction.setEffectiveWeight(0);

      return { root, mixer, idleAction, walkAction, cid: null };
    }

    function releaseInstance(entry) {
      entry.cid = null;
      entry.root.visible = false;
    }

    function acquireInstance(cid) {
      let entry = activeByCid.get(cid);
      if (entry) return entry;
      entry = pool.find((e) => e.cid === null);
      if (!entry) {
        entry = spawnInstance();
        pool.push(entry);
      }
      entry.cid = cid;
      entry.root.visible = true;
      entry.root.position.copy(ENTRANCE); // walk in from the entrance, not wherever this pool slot last was
      activeByCid.set(cid, entry);
      return entry;
    }

    const reconstructor = createCustomerReconstructor(trace ?? []);

    function resizeToContainer() {
      const { clientWidth, clientHeight } = container;
      if (!clientWidth || !clientHeight) return;
      camera.aspect = clientWidth / clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(clientWidth, clientHeight);
    }

    const resizeObserver = new ResizeObserver(resizeToContainer);
    resizeObserver.observe(container);
    resizeToContainer();

    let rafId;
    const tick = (ts) => {
      timer.update(ts);
      const delta = timer.getDelta();

      if (trace && trace.length > 0 && template) {
        reconstructor.advanceTo(currentTimeRef.current);
        const snapshot = reconstructor.getSnapshot();
        const targets = layoutPositions(snapshot, numBaristas);

        const seenCids = new Set(targets.keys());
        for (const [cid, target] of targets) {
          const entry = acquireInstance(cid);
          const dx = target.x - entry.root.position.x;
          const dz = target.z - entry.root.position.z;
          const dist = Math.hypot(dx, dz);

          entry.root.position.lerp(new THREE.Vector3(target.x, target.y, target.z), LERP_FACTOR);

          if (dist > WALK_EPSILON) {
            const heading = Math.atan2(dx, dz);
            entry.root.rotation.y = heading;
            entry.walkAction.setEffectiveWeight(1);
            entry.idleAction.setEffectiveWeight(0);
          } else {
            entry.walkAction.setEffectiveWeight(0);
            entry.idleAction.setEffectiveWeight(1);
          }
          entry.mixer.update(delta);
        }

        for (const [cid, entry] of activeByCid) {
          if (!seenCids.has(cid)) {
            releaseInstance(entry);
            activeByCid.delete(cid);
          }
        }

        stationMarkers.forEach((marker, slot) => {
          const busy = snapshot.stations.some(([s]) => s === slot);
          marker.material.color.copy(busy ? busyColor : idleColor);
        });
      }

      renderer.render(scene, camera);
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      resizeObserver.disconnect();

      pool.forEach((entry) => {
        entry.mixer.stopAllAction();
        entry.root.traverse((obj) => {
          if (obj.isMesh) {
            obj.geometry?.dispose();
            if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
            else obj.material?.dispose();
          }
        });
        scene.remove(entry.root);
      });

      ground.geometry.dispose();
      ground.material.dispose();
      counter.geometry.dispose();
      counter.material.dispose();
      stationMarkers.forEach((marker) => {
        marker.geometry.dispose();
        marker.material.dispose();
      });

      renderer.dispose();
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [trace, numBaristas, resetKey, maxConcurrency]);

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <h3 className="mb-1 text-sm font-medium text-[var(--text-primary)]">
        BrewLine digital twin
      </h3>
      <div ref={containerRef} className="h-[360px] w-full overflow-hidden rounded-md" />
    </div>
  );
}
