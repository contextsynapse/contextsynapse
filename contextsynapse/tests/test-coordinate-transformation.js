// Test script to verify coordinate transformation logic
// This replicates the exact same math as your React app

console.log('🧪 Testing Coordinate Transformation Logic\n');

// Simulate the exact same function as in App.js
function getElementAtPixel(canvasX, canvasY, pan, zoom, canvas, nodes) {
  console.log('=== STARTING CLICK DETECTION ===');
  
  // Convert to graph coordinates (accounting for pan/zoom only)
  const x = (canvasX - pan.x) / zoom;
  const y = (canvasY - pan.y) / zoom;
  
  console.log('🔍 COORDINATE TRANSFORMATION:');
  console.log(`  Raw Input: canvasX=${canvasX}, canvasY=${canvasY}`);
  console.log(`  Canvas: ${canvas.width}x${canvas.height}`);
  console.log(`  Pan: x=${pan.x}, y=${pan.y}`);
  console.log(`  Zoom: ${zoom}`);
  console.log(`  Graph coordinates: x=${x.toFixed(2)}, y=${y.toFixed(2)}`);
  
  // Check nodes (same logic as App.js)
  if (nodes) {
    console.log('🎯 CHECKING NODES:');
    
    for (let i = nodes.length - 1; i >= 0; i--) {
      const node = nodes[i];
      const distance = Math.sqrt((x - node.x) ** 2 + (y - node.y) ** 2);
      
      console.log(`  Node ${i} (${node.id}):`);
      console.log(`    Node Position: x=${node.x}, y=${node.y}`);
      console.log(`    Click Position: x=${x.toFixed(2)}, y=${y.toFixed(2)}`);
      console.log(`    Distance: ${distance.toFixed(2)} px`);
      console.log(`    Within Radius (200px): ${distance <= 200}`);
      console.log(`    Delta X: ${(x - node.x).toFixed(2)}`);
      console.log(`    Delta Y: ${(y - node.y).toFixed(2)}`);
      
      if (distance <= 200) {
        console.log(`✅ NODE SELECTED: ${node.id} (distance: ${distance.toFixed(2)})`);
        return { type: 'node', element: node };
      }
    }
    
    console.log('❌ NO NODES WITHIN RADIUS');
  }
  
  return null;
}

// Test data - simulate nodes
const testNodes = [
  { id: 'node1', x: 452.69, y: 484.90 },  // From your real data
  { id: 'node2', x: 611.78, y: 465.18 }   // From your real data
];

// Test scenario 1: Normal canvas click
console.log('🧪 TEST 1: Normal click inside canvas');
const pan = { x: 156.92, y: 46.87 };
const zoom = 0.8;
const canvas = { width: 1200, height: 800 };
const clickX = 500;
const clickY = 400;  // Well within canvas bounds

getElementAtPixel(clickX, clickY, pan, zoom, canvas, testNodes);

console.log('\n' + '='.repeat(60) + '\n');

// Test scenario 2: Problematic click (from your real issue)
console.log('🧪 TEST 2: Your problematic click');
const problematicClickY = 1593;
const problematicCanvasX = 528;

getElementAtPixel(problematicCanvasX, problematicClickY, pan, zoom, canvas, testNodes);

console.log('\n' + '='.repeat(60) + '\n');

// Test scenario 3: Canvas center click
console.log('🧪 TEST 3: Canvas center click');
const centerX = canvas.width / 2;
const centerY = canvas.height / 2;

getElementAtPixel(centerX, centerY, pan, zoom, canvas, testNodes);

console.log('\n🎯 ANALYSIS:');
console.log('If TEST 1 works but TEST 2 fails, the issue is clicking outside canvas bounds.');
console.log('If TEST 3 works, the center transformation is correct.');
console.log('Expected result: Only TEST 2 should fail (Y outside canvas bounds).');
