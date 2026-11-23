/* Configured channels for the admin channel pickers.

   The list comes from the backend's AUTH_CHANNEL config, so the panel never
   hardcodes channel ids and stays in sync with the bot. */
import { useEffect, useState } from "react";
import { api } from "./api";

let cache = null;

export function clearChannelCache() {
  cache = null;
}

export function useChannels() {
  const [channels, setChannels] = useState(cache || []);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    if (cache) {
      setChannels(cache);
      return () => {
        alive = false;
      };
    }
    api
      .get("/admin/api/channels")
      .then((d) => {
        if (!alive) return;
        const rows = Array.isArray(d.channels) ? d.channels : [];
        cache = rows;
        setChannels(rows);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { channels, error };
}

/* Build a t.me post link from a channel entry. Used to prefill the manual-add
   and bulk-link forms when a channel is picked. */
export function channelLink(row) {
  if (!row) return "";
  return row.link || `https://t.me/c/${row.clean_id || row.id}/`;
}
