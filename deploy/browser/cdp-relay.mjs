// Relay only to the owned browser; it remains on its original internal network.
import net from 'node:net';

const browserIp = process.argv[2];
net.createServer(client => {
  const upstream = net.connect(9223, browserIp);
  client.pipe(upstream).pipe(client);
  client.on('error', () => upstream.destroy());
  upstream.on('error', () => client.destroy());
  client.on('close', () => upstream.destroy());
}).listen(9223, '0.0.0.0');
