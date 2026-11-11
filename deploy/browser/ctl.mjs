import net from 'node:net';

let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 16_384) throw new Error('action too large');
}
const result = await new Promise((resolve, reject) => {
  const socket = net.connect(process.env.TAP_BROWSER_SOCKET || '/tmp/inspector.sock');
  let output = '';
  socket.setTimeout(90_000, () => socket.destroy(new Error('inspector timeout')));
  socket.on('connect', () => socket.write(input + '\n'));
  socket.on('data', chunk => { output += chunk; if (output.length > 100_000_000) socket.destroy(new Error('observation too large')); });
  socket.on('end', () => resolve(output));
  socket.on('error', reject);
});
process.stdout.write(result);
