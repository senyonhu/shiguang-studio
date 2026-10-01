VAR player = "旅人"
-> opening
=== opening ===
旁白：你醒来时，掌心压着冰冷的石板。头顶没有熟悉的天花板，只有一圈已经熄灭的灯。 # face:worried
璃：听得见吗？先别动，前面是断掉的台阶。
* [这是哪里？]
    璃：旧塔的下层。等有了光，我带你找出口。
* [你是谁？]
    璃：我叫璃。至于你为什么在这里……我也想知道。
- 璃：你手边的术式板还在响应。试着把第一盏灯的编号发给它。
终端：第一束光。 # task:tower_light_01
-> DONE
=== after_first ===
璃：亮了。它读懂了你写的东西。 # face:relieved
{player}：我只是改了一个数字。
璃：在这里，一个数字就能决定我们看见什么。
璃：这段旧术式应该依次点亮三盏灯。先运行一次，看看它停在哪里。 # face:thinking
终端：修复回廊。 # task:tower_light_02
-> DONE
=== after_second ===
璃：第三盏也亮了。但出口接的是另一条回路。 # face:relieved
璃：先别急着复制。只有一盏灯的时候呢？一盏也没有的时候呢？ # face:thinking
终端：点亮整座塔。 # task:tower_light_03
-> DONE
=== after_third ===
璃：现在，不管接上哪条回路，它都知道该做什么。 # face:relieved
璃：走吧。你总得先看看自己来到了什么地方。
旁白：门后的风，带来了清晨的气息。 # cinematic
-> DONE
=== echo ===
未知残响：如果你已经走出了那座塔……这一次，别把重要的话留到明天。 # echo
{player}：谁在说话？
璃：怎么了？ # face:worried
{player}：……没什么。
旁白：第一章·初识术式，待续。 # ending
-> DONE
