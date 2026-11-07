#!/bin/sh
set -eu
proxy_ip="$1"
proxy_port="$2"
iptables -P OUTPUT DROP
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -p tcp -d "$proxy_ip" --dport "$proxy_port" -j ACCEPT
ip6tables -P OUTPUT DROP
ip6tables -A OUTPUT -o lo -j ACCEPT
ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
node -e 'const net=require("node:net"); const s=net.connect({host:process.argv[1],port:Number(process.argv[2])});s.setTimeout(3000);s.on("connect",()=>{s.destroy();process.exit(0)});s.on("error",()=>process.exit(1));s.on("timeout",()=>process.exit(1))' "$proxy_ip" "$proxy_port"
node -e 'const net=require("node:net");const s=net.connect({host:"1.1.1.1",port:80});s.setTimeout(1000);s.on("connect",()=>process.exit(1));s.on("error",()=>process.exit(0));s.on("timeout",()=>process.exit(0))'
