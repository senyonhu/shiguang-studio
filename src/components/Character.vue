<script setup lang="ts">defineProps<{face:string}>();</script>
<template><svg class="character" viewBox="0 0 430 740" aria-label="守塔学徒璃" role="img">
<defs><linearGradient id="hair" x2="1" y2="1"><stop stop-color="#c2d4d5"/><stop offset=".45" stop-color="#93b3bd"/><stop offset="1" stop-color="#446875"/></linearGradient><linearGradient id="coat" x2=".8" y2="1"><stop stop-color="#315361"/><stop offset="1" stop-color="#142d3a"/></linearGradient><linearGradient id="cloak" x2="1" y2="1"><stop stop-color="#426575"/><stop offset=".5" stop-color="#1e3c4e"/><stop offset="1" stop-color="#102734"/></linearGradient><linearGradient id="skin" x2="1" y2=".5"><stop stop-color="#edd3c2"/><stop offset="1" stop-color="#bd9d93"/></linearGradient><radialGradient id="gem"><stop stop-color="#d1fcdf"/><stop offset=".5" stop-color="#71bea9"/><stop offset="1" stop-color="#376b6c"/></radialGradient></defs>
<!-- One original vector portrait, with three expressions. -->
<path d="M113 302Q68 249 94 145Q111 78 208 75Q304 79 326 168Q352 250 309 354L342 552L102 561Z" fill="#335562" stroke="#0d2735" stroke-width="4"/>
<path d="M160 265L123 303L84 360L30 620L52 740H380L402 600L339 350L278 296Z" fill="url(#cloak)" stroke="#102630" stroke-width="4"/>
<path d="M164 277L210 321L274 278L291 373L250 464L281 740H136L162 482L134 371Z" fill="#bcc2b9"/>
<path d="M188 255L187 307Q216 335 244 303L243 245Z" fill="url(#skin)"/>
<path d="M177 296L210 328L193 358L164 313ZM253 294L212 328L235 357L281 315" fill="#e5d8bf"/>
<path d="M161 296L187 366L163 481L185 740H78L105 459L95 351ZM276 291L238 366L263 489L240 740H353L324 462L344 359Z" fill="url(#coat)"/>
<path d="M156 306L181 368L150 477L166 684M284 306L247 367L276 477L258 686" fill="none" stroke="#c0a778" stroke-width="4"/>
<path d="M109 314Q72 321 59 378L130 420L166 346Z" fill="#385563" stroke="#9c977d" stroke-width="2"/><path d="M286 316L262 348L316 418L372 372Q353 320 311 310" fill="#385563" stroke="#9c977d" stroke-width="2"/>
<path d="M69 384L29 615L57 642L130 410ZM366 381L400 603L371 638L315 410" fill="url(#cloak)"/>
<path d="M58 604L91 625L80 662L49 648Z" fill="#dbc6b1"/><path d="M365 604L339 625L350 663L384 644Z" fill="#c4b3a5"/>
<path d="M139 500L279 500L282 528L136 528Z" fill="#3d3f3c" stroke="#af9668" stroke-width="3"/><rect x="194" y="497" width="40" height="35" rx="3" fill="#b89c68"/><rect x="203" y="504" width="22" height="20" fill="#3c4845"/>
<path d="M139 514L96 661L131 679L167 524" fill="#322f30" stroke="#7a6e56" stroke-width="2"/><path d="M83 643L134 655L127 712L71 699Z" fill="#695b47" stroke="#a48c65" stroke-width="2"/><path d="M83 643L133 655L105 677Z" fill="#837054"/>
<path d="M187 355Q218 376 246 351" fill="none" stroke="#b6a070" stroke-width="3"/><path d="M211 358L223 374L212 394L201 376Z" fill="url(#gem)" stroke="#e1c585" stroke-width="3"/>
<path d="M130 164Q119 216 143 255L186 292Q213 307 241 288L282 248Q300 218 287 163Z" fill="url(#skin)" stroke="#69818a" stroke-width="2"/>
<path d="M125 203Q107 191 114 220Q118 238 137 237M286 201Q306 186 297 218Q293 239 282 236" fill="#c2a79d"/>
<path d="M110 197Q98 141 135 109Q177 77 224 86Q291 85 317 147L301 242L274 204L277 150L250 192L229 140L209 186L195 130L163 181L147 161L132 236Z" fill="url(#hair)" stroke="#2c5061" stroke-width="3"/>
<path d="M133 116Q107 157 126 230L119 323L152 287L149 216M277 130Q310 164 287 253L280 321L323 279L314 193" fill="url(#hair)" stroke="#426675" stroke-width="2"/>
<path d="M148 123Q174 96 216 99M285 132L294 158M174 138L165 158M253 124L248 149" fill="none" stroke="#d9e4df" stroke-opacity=".65" stroke-width="3"/>
<path d="M277 137L303 158L293 179L267 153Z" fill="#25414a" stroke="#b8a578" stroke-width="3"/><path d="M285 141L286 169M273 149L298 159" stroke="#c2ad77" stroke-width="2"/>
<g v-if="face==='relieved'"><path d="M157 210Q173 195 188 209M232 209Q248 195 263 207" fill="none" stroke="#304e58" stroke-width="3"/><path d="M199 261Q212 271 226 258" fill="none" stroke="#936f6b" stroke-width="2"/><path d="M157 192L184 191M236 188L262 191" fill="none" stroke="#637d84" stroke-width="2"/></g>
<g v-else><path d="M153 207Q169 197 189 208M231 208Q248 197 266 205" fill="#e2e7dc" stroke="#3b5158" stroke-width="2"/><ellipse cx="174" cy="209" rx="7" ry="11" fill="#427f7a"/><ellipse cx="247" cy="208" rx="7" ry="11" fill="#427f7a"/><ellipse cx="175" cy="211" rx="3" ry="7" fill="#1f414a"/><ellipse cx="247" cy="210" rx="3" ry="7" fill="#1f414a"/><circle cx="176" cy="205" r="2.5" fill="#e9f4e9"/><circle cx="249" cy="204" r="2.5" fill="#e9f4e9"/><path :d="face==='worried'?'M157 191L188 185M233 185L263 190':'M156 189L188 191M233 191L263 185'" fill="none" stroke="#587580" stroke-width="2.5"/><path :d="face==='worried'?'M201 263Q212 257 225 262':'M203 260L222 260'" fill="none" stroke="#936f6b" stroke-width="2"/></g>
<path d="M211 224L206 241L214 242" fill="none" stroke="#b1968d" stroke-width="1.5"/><ellipse cx="161" cy="237" rx="13" ry="4" fill="#ce9e90" opacity=".25"/><ellipse cx="258" cy="236" rx="12" ry="4" fill="#ce9e90" opacity=".25"/>
<path d="M299 243L308 336L285 380L278 327Z" fill="#6f95a3" stroke="#274653" stroke-width="3"/><path d="M292 294L304 304M287 317L299 327M284 340L295 348" stroke="#becfc8" stroke-width="3"/><path d="M281 365L295 368L293 385L280 381Z" fill="#be9e62"/>
<g fill="none" stroke="#647d81" opacity=".4"><path d="M111 540L91 600M135 567L123 614M301 555L323 633M67 448L54 523M347 447L366 530"/></g>
</svg></template>
