'use strict'

Vue.component('asset-preview', {
  template: `
    <div class='asset-live panel panel-default'>
      <div class='panel-heading'>
        Project by <a :href='"https://github.com/" + asset.user'>{{asset.user}}</a>
      </div>
      <div class='panel-body'>
        <a :href='asset.url'>
          <img class='img-responsive' :src='asset.thumb + "?size=328&crop=none"'>
        </a>
      </div>
    </div>
  `,
  props: ['asset'],
})

function unix() {
  return +new Date()/1000
}

Vue.component('list-last', {
  template: `
    <div class='row'>
      <div class='col-md-2' v-for='[room, proofs] in last'>
        <h3>{{room}}</h3>
        <p :key='proof.id' v-for='proof in proofs'>
          <a :href='proof.url'>
            <img :src='proof.thumb + "?size=194&crop=none"'>
          </a>
          <small>
            <a :href='"https://github.com/" + proof.user'>{{proof.user}}</a>,
            {{now - proof.shown|floor}}s ago 
          </small>
        </p>
      </div>
    </div>
  `,
  data: () => ({
    last: [],
    now: unix(),
  }),
  created() {
    this.update_interval = setInterval(this.update, 20000)
    this.now_interval = setInterval(() => {
      this.now = unix()
    }, 1000)
    this.update()
  },
  methods: {
    async update() {
      const r = await Vue.http.get('content/last')
      this.last = r.data.last
    },
  },
})

Vue.filter('floor', function(v) {
  return Math.floor(v)
})

Vue.component('list-active', {
  template: `
    <div class='row'>
      <template v-if='assets.length > 0'>
        <div class='col-md-4' v-for='asset in assets'>
          <asset-preview :asset='asset'/>
        </div>
      </template>
      <div class='col-md-12' v-else>
        <div class='alert alert-info'>
          No uploads yet.
        </div>
      </div>
    </div>
  `,
  data: () => ({
    assets: [],
  }),
  async created() {
    const r = await Vue.http.get('content/live')
    this.assets = r.data.assets
  }
})

new Vue({el: "#main"})
