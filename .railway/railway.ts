import {
  defineRailway,
  github,
  preserve,
  project,
  service,
  volume,
} from "railway/iac";

export default defineRailway(() => {
  const data = volume("pumpguard-data", {
    region: "europe-west4",
    sizeMB: 1024,
  });

  const bot = service("pumpguard-bot", {
    source: github("AtenovD/PumpGuard-AI", { branch: "main" }),
    start: "python -m bot.main",
    volumeMounts: {
      "/data": data,
    },
    env: {
      BOT_TOKEN: preserve(),
      ADMIN_IDS: preserve(),
      GROK_API_KEY: preserve(),
      DB_PATH: "/data/pumpguard.db",
      ENABLED_CHAINS: "robinhood",
    },
  });

  return project("PumpGuard-AI", {
    resources: [bot, data],
  });
});
