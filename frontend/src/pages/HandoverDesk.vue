<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const shifts = ref([])
const fromId = ref(1)
const toId = ref(2)
const current = ref(null)
const qualifications = ref({ 矩阵: [] })
const msg = ref('')
const ok = ref(false)

async function refresh() {
  shifts.value = await getJson('/api/shifts')
  qualifications.value = await getJson('/api/qualifications')
  const logs = await getJson('/api/handovers')
  current.value = logs.find((h) => h.status === '进行中') || null
  if (current.value) {
    current.value = await getJson(`/api/handovers/${current.value.id}`)
  }
}

// 清单预览：交班方名下在途批次覆盖的责任区，以及双方当前资格
const jobsByShift = ref([])
const previewReady = ref(false)

const preview = computed(() => {
  const matrix = {}
  for (const c of qualifications.value.矩阵 || []) {
    matrix[`${c.shift_id}-${c.zone_id}`] = c
  }
  const zones = qualifications.value.责任区 || []
  const zoneIds = jobsByShift.value.map((b) => b.zone_id)
  const uniq = [...new Set(zoneIds.filter((z) => z != null))]
  const rows = uniq.map((zid) => {
    const z = zones.find((x) => x.id === zid)
    return {
      zone_id: zid,
      zone_name: z?.name || `#${zid}`,
      zone_code: z?.code || '',
      from_ok: !!matrix[`${fromId.value}-${zid}`]?.qualified,
      to_ok: !!matrix[`${toId.value}-${zid}`]?.qualified,
    }
  })
  return rows
})

// 未标责任区的在途批次无法校验资格，与后端一致按整单拒绝处理
const unzonedCount = computed(
  () => jobsByShift.value.filter((b) => b.zone_id == null).length
)

async function loadPreview() {
  previewReady.value = false
  try {
    jobsByShift.value = await getJson(`/api/batches?owner_shift_id=${fromId.value}`)
    jobsByShift.value = jobsByShift.value.filter((b) => b.status === '在途')
  } catch {
    jobsByShift.value = []
  }
  previewReady.value = true
}

const blocked = computed(
  () => unzonedCount.value > 0 || preview.value.some((r) => !r.from_ok || !r.to_ok)
)

onMounted(async () => {
  try {
    await refresh()
    await loadPreview()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

async function onPairChange() {
  msg.value = ''
  ok.value = false
  await loadPreview()
}

async function start() {
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson('/api/handovers/start', {
      from_shift_id: Number(fromId.value),
      to_shift_id: Number(toId.value),
    })
    ok.value = true
    msg.value = `已开交班单 #${current.value.id}，清单 ${current.value.batch_count} 批`
    await refresh()
    await loadPreview()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function complete() {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson(`/api/handovers/${current.value.id}/complete`)
    ok.value = current.value.status === '已完成'
    msg.value = current.value.status === '已完成'
      ? `交班单 #${current.value.id} 已完成，批次已过户`
      : `交班单 #${current.value.id} 失败：${current.value.reason || '归属不一致'}`
    await refresh()
    await loadPreview()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>交班办理</h1>
    <p class="lead">同一时刻只允许一单进行中的交班；清单内在途批次必须整批过户，禁止只改单据不改归属。</p>
    <p class="lead">
      清单内每个责任区，交班方与接班方都必须具备资格；任一不满足即整单拒绝发起。
      完成阶段只按发起时固化的资格快照解释。
    </p>

    <div class="panel">
      <div class="row">
        <label>交班方
          <select v-model.number="fromId" @change="onPairChange">
            <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <label>接班方
          <select v-model.number="toId" @change="onPairChange">
            <option v-for="s in shifts" :key="'t'+s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <button class="btn" :disabled="!!current" @click="start">发起交班</button>
        <button class="btn ghost" :disabled="!current" @click="complete">完成交班</button>
      </div>
      <p class="msg" :class="{ ok }">{{ msg }}</p>
    </div>

    <div class="panel" v-if="previewReady && !current">
      <h2>清单资格预检</h2>
      <p class="hint">交班方在途批次覆盖 {{ preview.length }} 个责任区；双方均为「可」才允许发起。</p>
      <p v-if="unzonedCount" class="msg">有 {{ unzonedCount }} 个在途批次未标责任区，无法校验资格，将整单拒绝。</p>
      <table>
        <thead>
          <tr><th>责任区</th><th>交班方资格</th><th>接班方资格</th><th>结论</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in preview" :key="r.zone_id">
            <td>{{ r.zone_name }} <span class="sub">{{ r.zone_code }}</span></td>
            <td><span class="tag" :class="{ on: r.from_ok }">{{ r.from_ok ? '可' : '否' }}</span></td>
            <td><span class="tag" :class="{ on: r.to_ok }">{{ r.to_ok ? '可' : '否' }}</span></td>
            <td>
              <span class="tag" :class="{ on: r.from_ok && r.to_ok }">
                {{ r.from_ok && r.to_ok ? '可交班' : '将整单拒绝' }}
              </span>
            </td>
          </tr>
          <tr v-if="!preview.length"><td colspan="4" class="hint">交班方没有在途批次</td></tr>
        </tbody>
      </table>
      <p v-if="blocked" class="msg">存在资格缺口：发起会被整单拒绝，请先到「资格矩阵」维护。</p>
    </div>

    <div class="panel" v-if="current">
      <h2>当前交班单 #{{ current.id }} · {{ current.status }}</h2>
      <p class="lead" style="margin-bottom:12px">
        {{ current.from_shift_name }} → {{ current.to_shift_name }} · 清单 {{ current.batch_count }} 批
      </p>
      <table>
        <thead><tr><th>业务键</th><th>标题</th><th>责任区</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in current.items || []" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.zone_name || '—' }}</td>
            <td>{{ b.status }}</td>
          </tr>
        </tbody>
      </table>

      <h2 style="margin-top:18px">资格快照（发起时固化）</h2>
      <table>
        <thead><tr><th>双方</th><th>班次</th><th>责任区</th><th>资格</th></tr></thead>
        <tbody>
          <tr v-for="s in current.资格快照 || []" :key="`${s.side}-${s.zone_id}`">
            <td>{{ s.双方 }}</td>
            <td>{{ s.shift_name }}</td>
            <td>{{ s.zone_name }}</td>
            <td><span class="tag" :class="{ on: s.qualified }">{{ s.资格 }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
