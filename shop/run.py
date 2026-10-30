"""Starts all services. Usage: python run.py   then open http://localhost:8000"""
import subprocess, sys, os, time
S = [("auth", 8001), ("products", 8002), ("inventory", 8003), ("orders", 8004), ("coupons", 8005), ("returns", 8006), ("analytics", 8007), ("payments", 8008), ("notifications", 8009), ("reviews", 8010), ("cart", 8011), ("gateway", 8000)]
env = {**os.environ, "JWT_SECRET": os.getenv("JWT_SECRET", "dev-secret"), "INTERNAL_KEY": os.getenv("INTERNAL_KEY", "internal")}
ps = [subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(p)], cwd=n, env=env) for n, p in S]
print("\n>>> Open http://localhost:8000  (admin@example.com / Admin@123)  Ctrl+C to stop\n")
try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    [p.terminate() for p in ps]
