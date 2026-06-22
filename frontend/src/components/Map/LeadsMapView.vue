<template>
  <div class="flex h-full flex-1 flex-col overflow-hidden">
    <!-- Truncation / empty banners -->
    <div
      v-if="truncated"
      class="flex items-center gap-2 border-b border-outline-gray-1 bg-surface-amber-1 px-4 py-2 text-base text-ink-amber-3"
    >
      <FeatherIcon name="alert-triangle" class="h-4 w-4 shrink-0" />
      <span>
        {{
          __(
            'Showing {0} of {1} leads with a location. Refine your filters to see the rest.',
            [markers.length, totalWithCoords],
          )
        }}
      </span>
    </div>

    <div v-if="!markers.length" class="flex flex-1 items-center justify-center">
      <div class="flex flex-col items-center gap-2 text-ink-gray-4">
        <FeatherIcon name="map-pin" class="h-8 w-8" />
        <span class="text-base">{{ __('No leads with a location') }}</span>
      </div>
    </div>

    <div v-show="markers.length" :id="mapId" class="min-h-0 w-full flex-1" />
  </div>
</template>

<script setup>
// Static ?url imports so Vite bundles the marker images and resolves paths.
import leafletIconUrl from 'leaflet/dist/images/marker-icon.png?url'
import leafletIconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png?url'
import leafletShadowUrl from 'leaflet/dist/images/marker-shadow.png?url'
import { FeatherIcon } from 'frappe-ui'
import { usersStore } from '@/stores/users'
import { statusesStore } from '@/stores/statuses'
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'

const leads = defineModel()

const props = defineProps({
  options: {
    type: Object,
    default: () => ({}),
  },
})

const router = useRouter()
const { getUser } = usersStore()
const { getLeadStatus } = statusesStore()

const mapId = `leads-map-${Math.random().toString(36).slice(2)}`

// Leaflet instances — populated after dynamic import
let L = null
let mapInstance = null
let markerLayer = null

// ─── Data ────────────────────────────────────────────────────────────────────

// Raw leads returned by the map view (already filtered server-side to leads
// that have a latitude). Keep only rows with valid, finite coordinates.
const markers = computed(() => {
  const rows = leads.value?.data?.data || []
  return rows
    .map((row) => ({
      ...row,
      lat: parseFloat(row.custom_latitude),
      lng: parseFloat(row.custom_longitude),
    }))
    .filter((row) => isFinite(row.lat) && isFinite(row.lng))
})

// total_count comes back filtered to coordinate-bearing leads, so it is the
// true total; markers.length is the (capped) number actually plotted.
const totalWithCoords = computed(() => leads.value?.data?.total_count || 0)
const truncated = computed(() => totalWithCoords.value > markers.value.length)

// ─── Map lifecycle ────────────────────────────────────────────────────────────

async function initMap() {
  if (!L) {
    await import('leaflet/dist/leaflet.css')
    const leafletModule = await import('leaflet')
    L = leafletModule.default ?? leafletModule

    // Fix Vite marker image paths — drop the built-in resolver, then supply
    // the already-resolved ?url import strings.
    delete L.Icon.Default.prototype._getIconUrl
    L.Icon.Default.mergeOptions({
      iconUrl: leafletIconUrl,
      iconRetinaUrl: leafletIconRetinaUrl,
      shadowUrl: leafletShadowUrl,
    })
  }

  if (mapInstance) return

  mapInstance = L.map(mapId, { scrollWheelZoom: true })

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution:
      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(mapInstance)

  markerLayer = new L.FeatureGroup().addTo(mapInstance)

  renderMarkers()
}

function popupHtml(row) {
  const status = getLeadStatus(row.status)?.stage_label || row.status || ''
  const owner = row.lead_owner ? getUser(row.lead_owner)?.full_name || '' : ''
  const lines = [
    `<div class="font-medium text-ink-gray-9">${escapeHtml(
      row.lead_name || row.name,
    )}</div>`,
  ]
  if (row.organization) lines.push(`<div>${escapeHtml(row.organization)}</div>`)
  if (status) lines.push(`<div>${escapeHtml(status)}</div>`)
  if (row.mobile_no) lines.push(`<div>${escapeHtml(row.mobile_no)}</div>`)
  if (owner) lines.push(`<div>${escapeHtml(owner)}</div>`)
  lines.push(
    `<a href="#" data-lead-open="${escapeHtml(
      row.name,
    )}" class="mt-1 inline-block font-medium text-ink-blue-3">${__('Open lead')}</a>`,
  )
  return `<div class="flex flex-col gap-0.5 text-sm">${lines.join('')}</div>`
}

function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[c],
  )
}

function renderMarkers() {
  if (!L || !mapInstance || !markerLayer) return

  markerLayer.clearLayers()

  markers.value.forEach((row) => {
    const marker = L.marker([row.lat, row.lng]).bindPopup(popupHtml(row))
    marker.leadRow = row
    markerLayer.addLayer(marker)
  })

  // Wire the "Open lead" link inside each popup to the router.
  mapInstance.off('popupopen')
  mapInstance.on('popupopen', (e) => {
    const link = e.popup.getElement()?.querySelector('a[data-lead-open]')
    if (!link) return
    link.onclick = (ev) => {
      ev.preventDefault()
      const route = props.options.getRoute?.({
        name: link.getAttribute('data-lead-open'),
      })
      if (route) router.push(route)
    }
  })

  mapInstance.invalidateSize()
  const bounds = markerLayer.getBounds()
  if (bounds.isValid()) {
    if (markers.value.length === 1) {
      mapInstance.setView(bounds.getCenter(), 14)
    } else {
      mapInstance.fitBounds(bounds, { padding: [50, 50] })
    }
  } else {
    // Fallback view (India) when there is nothing to show yet.
    mapInstance.setView([22.97, 78.65], 5)
  }
}

watch(markers, () => {
  if (mapInstance) {
    nextTick(renderMarkers)
  } else {
    nextTick(initMap)
  }
})

onMounted(() => nextTick(initMap))

onBeforeUnmount(() => {
  if (mapInstance) {
    mapInstance.remove()
    mapInstance = null
    markerLayer = null
  }
})
</script>
